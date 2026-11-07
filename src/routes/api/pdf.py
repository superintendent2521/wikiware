"""API endpoints for PDF generation from Markdown files."""

import io
import re
import asyncio
import html
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Set, Tuple
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from loguru import logger
from markdown_pdf import MarkdownPdf, Section
from starlette.concurrency import run_in_threadpool
from ...middleware.auth_middleware import AuthMiddleware
from ...middleware.rate_limiter import rate_limit
from ...services.render_service import render_markdown, render_sources_html
from ...services.storage_service import StorageError, download_image_bytes
from ...utils.csrf import require_csrf


MAX_LINKED_PAGES = 5
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_ANCHOR_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
_PDF_LIMIT = asyncio.Semaphore(1)


def _normalize_key(title: str, branch: str) -> Tuple[str, str]:
    """Return a normalized key for title/branch combinations."""
    norm_title = (title or "").strip().lower()
    norm_branch = (branch or "main").strip().lower() or "main"
    return norm_title, norm_branch


def _slugify_anchor(title: str, branch: str) -> str:
    """Create a predictable anchor id for a page title/branch."""
    base = (
        _ANCHOR_SANITIZE_RE.sub("-", (title or "").strip().lower()).strip("-") or "page"
    )
    branch_norm = (branch or "main").strip().lower()
    if branch_norm != "main":
        suffix = _ANCHOR_SANITIZE_RE.sub("-", branch_norm).strip("-")
        if suffix:
            base = f"{base}-{suffix}"
    return base


def _extract_wiki_links(content: str, current_branch: str) -> List[Tuple[str, str]]:
    """Return titles/branches referenced via [[Page]] syntax."""
    if not content:
        return []
    matches = _WIKI_LINK_RE.findall(content)
    if not matches:
        return []
    links: List[Tuple[str, str]] = []
    default_branch = "main"
    for raw in matches:
        body = raw.strip()
        if not body:
            continue
        if ":" in body:
            title_part, branch_part = body.split(":", 1)
            title = title_part.strip()
            branch = branch_part.strip() or default_branch
        else:
            title = body
            branch = default_branch
        title = title.strip()
        if not title:
            continue
        links.append((title, branch or "main"))
    return links


def _build_anchor_lookup(
    pages: List[Dict[str, str]],
) -> Dict[Tuple[str, str], Dict[str, str]]:
    """Assign stable anchors and href fragments for collected pages."""
    anchors: Dict[Tuple[str, str], Dict[str, str]] = {}
    used_ids: Set[str] = set()
    for bundle in pages:
        key = _normalize_key(bundle["title"], bundle["branch"])
        anchor = _slugify_anchor(bundle["title"], bundle["branch"])
        base = anchor
        counter = 2
        while anchor in used_ids:
            anchor = f"{base}-{counter}"
            counter += 1
        used_ids.add(anchor)
        anchors[key] = {
            "anchor": anchor,
            "encoded_title": quote(bundle["title"], safe=""),
            "encoded_branch": quote(bundle["branch"], safe=""),
            "branch": bundle["branch"],
        }
    return anchors


def _rewrite_internal_links(
    content: str, anchors: Dict[Tuple[str, str], Dict[str, str]]
) -> str:
    """Swap HTML hrefs to internal anchors when target pages are bundled."""
    if not content:
        return content
    rewritten = content
    for data in anchors.values():
        encoded_title = re.escape(data["encoded_title"])
        encoded_branch = re.escape(data["encoded_branch"])
        anchor = data["anchor"]
        branch = (data["branch"] or "main").lower()
        if branch == "main":
            pattern = rf'<a href="/page/{encoded_title}(?:\?branch={encoded_branch})?">'
        else:
            pattern = rf'<a href="/page/{encoded_title}\?branch={encoded_branch}">'
        rewritten = re.sub(pattern, f'<a href="#{anchor}">', rewritten)
    return rewritten


async def _collect_linked_pages(
    root_title: str, root_branch: str, *, max_pages: int = MAX_LINKED_PAGES
) -> List[Dict[str, str]]:
    """Collect the root page and up to `max_pages`-1 linked pages."""
    limit = max(1, min(MAX_LINKED_PAGES, max_pages))
    queue: List[Tuple[str, str]] = []
    initial = (root_title or "").strip() or root_title
    branch = (root_branch or "main").strip() or "main"
    queue.append((initial, branch))
    queued: Set[Tuple[str, str]] = {_normalize_key(initial, branch)}
    logger.info(f"Starting PDF page crawl from {initial} (branch: {branch})")
    visited: Set[Tuple[str, str]] = set()
    collected: List[Dict[str, str]] = []

    while queue and len(collected) < limit:
        title, current_branch = queue.pop(0)
        key = _normalize_key(title, current_branch)
        queued.discard(key)
        if key in visited:
            continue
        visited.add(key)
        page = await PageService.get_page(title, current_branch)
        if not page:
            logger.warning(
                f"Linked page not found during PDF export: {title} ({current_branch})"
            )
            continue

        page_branch = page.get("branch") or current_branch
        raw_content = page.get("content", "") or ""
        processed, _, sources = await render_markdown(raw_content)
        processed += render_sources_html(sources)
        collected.append(
            {
                "title": page.get("title", title),
                "branch": page_branch,
                "raw": raw_content,
                "content": processed,
            }
        )
        logger.info(f"Collected page {title} (branch: {page_branch}) for PDF export")

        if len(collected) >= limit:
            break

        for linked_title, linked_branch in _extract_wiki_links(
            raw_content, page_branch
        ):
            child_key = _normalize_key(linked_title, linked_branch)
            if child_key in visited or child_key in queued:
                continue
            if len(collected) + len(queue) >= limit:
                continue
            queue.append((linked_title, linked_branch))
            queued.add(child_key)
            logger.info(
                f"Queued linked page {linked_title} (branch: {linked_branch}) for PDF export"
            )

    return collected


PDF_CSS = """
body { font-family: 'Helvetica', Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #1b1b1d; }
h1 { font-size: 24pt; margin: 0 0 12pt; }
h2 { font-size: 20pt; margin: 18pt 0 8pt; }
h3 { font-size: 16pt; margin: 16pt 0 6pt; }
h4, h5, h6 { font-size: 14pt; margin: 12pt 0 6pt; }
p { margin: 0 0 10pt; }
strong { font-weight: 600; }
em { font-style: italic; }
ul, ol { margin: 0 0 10pt 16pt; }
li { margin-bottom: 4pt; }
code { font-family: 'Fira Code', 'Courier New', monospace; font-size: 10pt; background-color: #f4f4f5; padding: 1pt 3pt; border-radius: 3pt; }
pre { background-color: #f4f4f5; padding: 8pt; border-radius: 4pt; overflow-x: auto; }
pre code { font-size: 10pt; background: transparent; padding: 0; }
blockquote { border-left: 3pt solid #d0d0d7; margin: 0 0 10pt; padding: 4pt 12pt; color: #555; }
img { max-width: 100%; margin: 12pt 0; }
table { border-collapse: collapse; width: 100%; margin: 12pt 0; }
thead tr { background-color: #f9f9fb; }
th, td { border: 1px solid #d7d7db; padding: 6pt 8pt; text-align: left; vertical-align: top; }
tbody tr:nth-child(even) { background-color: #fafafe; }
a { color: #0a66c2; text-decoration: none; }
a:hover { text-decoration: underline; }
hr { border: none; border-top: 1pt solid #d7d7db; margin: 20pt 0; }
.color-red { color: #d90429; }
.color-green { color: #2b9348; }
.color-blue { color: #277da1; }
.color-purple { color: #7209b7; }
.color-pink { color: #d81159; }
.color-orange { color: #f3722c; }
.color-yellow { color: #f9c74f; }
.color-gray { color: #6c757d; }
.color-cyan { color: #00b4d8; }
.unix-timestamp { color: #495057; font-size: 10pt; }
.unix-timestamp-error { color: #b00020; font-style: italic; font-size: 10pt; }
"""

from ...services.page_service import PageService

router = APIRouter()


class PDFRequest(BaseModel):
    title: str
    branch: str = "main"
    depth: int = 1

    @validator("depth", pre=True)
    def _validate_depth(cls, value):  # noqa: N805
        """Ensure the requested depth is within allowed bounds."""
        try:
            depth_int = int(value)
        except (TypeError, ValueError):
            depth_int = 1
        return max(1, min(MAX_LINKED_PAGES, depth_int))


async def _pdf_images(content: str) -> tuple[str, dict[str, bytes]]:
    """Only embed images from our storage, never paths or URLs authored by users."""
    assets = {}
    stored_names = {}
    replacements = {}
    total_bytes = 0
    for tag in dict.fromkeys(re.findall(r"<img\b[^>]*>", content)):
        match = re.search(r'\bsrc="([^"]*)"', tag)
        replacement = "<span>[Image unavailable in PDF]</span>"
        filename = None
        if match:
            source = html.unescape(match.group(1))
            try:
                parsed = urlsplit(source)
            except ValueError:
                parsed = None
            if parsed is not None and not parsed.scheme and not parsed.netloc:
                for prefix in ("/media/uploads/", "/static/uploads/"):
                    if parsed.path.startswith(prefix):
                        filename = unquote(parsed.path[len(prefix):])
                        break
        if filename and not any(char in filename for char in ("/", "\\", "..")) and not any(ord(char) < 32 or ord(char) == 127 for char in filename):
            suffix = Path(filename).suffix.lower()
            key = stored_names.get(filename)
            if key is None and suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} and len(assets) < 32:
                try:
                    data = await download_image_bytes(filename)
                except StorageError:
                    data = b""
                if data and len(data) <= 8 * 1024 * 1024 and total_bytes + len(data) <= 32 * 1024 * 1024:
                    key = f"image-{len(assets)}{suffix}"
                    assets[key] = data
                    stored_names[filename] = key
                    total_bytes += len(data)
            if key is not None:
                replacement = tag[:match.start(1)] + key + tag[match.end(1):]
        replacements[tag] = replacement
    return re.sub(r"<img\b[^>]*>", lambda match: replacements[match.group(0)], content), assets


def _build_pdf(content: str, assets: dict[str, bytes], title: str) -> bytes:
    with TemporaryDirectory(prefix="wikiware-pdf-") as directory:
        for filename, data in assets.items():
            (Path(directory) / filename).write_bytes(data)
        pdf = MarkdownPdf(mode="commonmark")
        pdf.m_d.options["html"] = True
        pdf.meta = {**pdf.meta, "title": title}
        pdf.add_section(Section(content, root=directory), user_css=PDF_CSS)
        # PyMuPDF requires the first level to be one and no skipped levels.
        previous_level = 0
        normalized = []
        for level, text, page, top in pdf.toc:
            level = min(max(1, level), previous_level + 1)
            normalized.append((level, text, page, top))
            previous_level = level
        pdf.toc = normalized
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


@router.post("/pdf/page", dependencies=[
    Depends(require_csrf),
    Depends(rate_limit("pdf-export", use_user_identity=True)),
])
async def generate_page_pdf(request: Request, pdf_req: PDFRequest):
    """Render a bounded PDF bundle with the shared wiki dialect."""
    await AuthMiddleware.require_auth(request)
    async with _PDF_LIMIT:
        bundles = await _collect_linked_pages(
            pdf_req.title, pdf_req.branch, max_pages=pdf_req.depth
        )
        if not bundles:
            raise HTTPException(status_code=404, detail="Page not found")
        anchors = _build_anchor_lookup(bundles)
        blocks = []
        for index, bundle in enumerate(bundles):
            anchor = anchors[_normalize_key(bundle["title"], bundle["branch"])]["anchor"]
            content = _rewrite_internal_links(bundle["content"], anchors)
            # Keep per-article headings and citation IDs unique within a bundle.
            content = re.sub(r'\bid="([^"]+)"', lambda match: f'id="{anchor}-{match[1]}"', content)
            content = re.sub(r'href="#([^"]+)"', lambda match: (
                match[0] if match[1] in {value["anchor"] for value in anchors.values()}
                else f'href="#{anchor}-{match[1]}"'
            ), content)
            title = bundle["title"]
            if bundle["branch"] != "main":
                title += f' ({bundle["branch"]})'
            page_break = '<div style="page-break-before: always;"></div>' if index else ""
            blocks.append(f'{page_break}<h1 id="{anchor}">{html.escape(title)}</h1>\n{content}')
        content, assets = await _pdf_images("\n\n".join(blocks))
        try:
            pdf_bytes = await run_in_threadpool(_build_pdf, content, assets, pdf_req.title)
        except Exception as exc:
            logger.exception("PDF generation failed: {}", exc)
            raise HTTPException(status_code=500, detail="Failed to generate PDF") from exc
    filename = quote(pdf_req.title + ".pdf", safe="")
    headers = {"Content-Disposition": f"attachment; filename=wikiware.pdf; filename*=UTF-8''{filename}"}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)
