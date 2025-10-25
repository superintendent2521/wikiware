"""API endpoints for PDF generation from Markdown files."""

import io
import re
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from loguru import logger
from markdown_pdf import MarkdownPdf, Section
from ...utils.link_processor import process_internal_links


_UNIX_TOKEN_RE = re.compile(r"\{\{\s*global\.unix(?::(\d+))?\s*\}}")
_GLOBAL_TOKEN_RE = re.compile(r"\{\{\s*global\.[^}}]+\}\}")

MAX_LINKED_PAGES = 5
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_ANCHOR_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _render_unix_tokens(content: str) -> str:
    """Render {{ global.unix:TIMESTAMP }} tokens to readable spans."""
    if not content:
        return content

    def _replace(match):
        timestamp_str = match.group(1)
        if not timestamp_str:
            return (
                '<span class="unix-timestamp-error" data-source="error" '
                'title="Timestamp missing for {{ global.unix }}">Invalid timestamp</span>'
            )
        try:
            ts = int(timestamp_str)
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            formatted = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
            return (
                f'<span class="unix-timestamp" '
                f'title="Unix timestamp: {ts}" data-timestamp="{ts}" '
                f'data-source="provided">{formatted}</span>'
            )
        except (ValueError, OSError):
            return (
                f'<span class="unix-timestamp-error" '
                f'title="Invalid timestamp: {timestamp_str}" data-timestamp="{timestamp_str}" '
                f'data-source="error">Invalid timestamp</span>'
            )

    return _UNIX_TOKEN_RE.sub(_replace, content)


def _strip_unrendered_tokens(content: str) -> str:
    """Remove unresolved {{ global.* }} tokens that would otherwise leak."""
    if not content:
        return content
    return _GLOBAL_TOKEN_RE.sub("", content)


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
    default_branch = (current_branch or "main").strip() or "main"
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
        unix_rendered = _render_unix_tokens(raw_content)
        processed = await process_internal_links(unix_rendered)
        processed = _strip_unrendered_tokens(processed)
        collected.append(
            {
                "title": page.get("title", title),
                "branch": page_branch,
                "raw": raw_content,
                "content": processed,
            }
        )
        logger.info(
            f"Collected page {title} (branch: {page_branch}) for PDF export"
        )

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


@router.post("/pdf/page")
async def generate_page_pdf(request: Request, pdf_req: PDFRequest):
    """Generate PDF for an authenticated user's requested page."""

    # user = await AuthMiddleware.require_auth(request)
    # username = user["username"]
    # logger.info(f"Generating PDF for page '{pdf_req.title}' (branch: {pdf_req.branch}) by user {username}")
    pages = await _collect_linked_pages(
        pdf_req.title, pdf_req.branch, max_pages=pdf_req.depth
    )
    if not pages:
        raise HTTPException(status_code=404, detail="Page not found")

    anchors = _build_anchor_lookup(pages)
    included_titles: List[str] = []
    assembled_blocks: List[str] = []

    for index, bundle in enumerate(pages):
        key = _normalize_key(bundle["title"], bundle["branch"])
        anchor_data = anchors.get(key)
        if not anchor_data:
            continue

        friendly_title = bundle["title"]
        if bundle["branch"] and bundle["branch"] != "main":
            friendly_title = f"{friendly_title} ({bundle['branch']})"

        content = _rewrite_internal_links(bundle["content"], anchors)
        content = _strip_unrendered_tokens(content)
        stripped = content.lstrip()

        segments = [f'<a id="{anchor_data["anchor"]}"></a>']
        if not stripped.startswith("#"):
            segments.append(f"# {friendly_title}")
        elif bundle["branch"] and bundle["branch"] != "main":
            segments.append(f"*Branch: {bundle['branch']}*")
        segments.append(content)

        block = "\n\n".join(part for part in segments if part)
        if index > 0:
            block = '<div style="page-break-before: always;"></div>\n\n' + block
        assembled_blocks.append(block)
        included_titles.append(friendly_title)

    combined_markdown = "\n\n".join(assembled_blocks)
    logger.info(
        "Generating PDF for page {} (branch: {}) including {}",
        pdf_req.title,
        pdf_req.branch,
        included_titles,
    )

    try:

        pdf = MarkdownPdf(mode="gfm-like")
        pdf.meta["title"] = pdf_req.title
        pdf.meta["subject"] = f"Includes {len(included_titles)} page(s)"
        section = Section(combined_markdown)
        pdf.add_section(section, user_css=PDF_CSS)
        if pdf.toc:
            # Normalize heading levels so PyMuPDF accepts the generated TOC.
            first_with_level = next((item[0] for item in pdf.toc if item[0] > 0), None)
            if first_with_level and first_with_level != 1:
                shift = first_with_level - 1
                pdf.toc = [
                    (max(1, level - shift), title, page, top)
                    for level, title, page, top in pdf.toc
                ]

        out = io.BytesIO()
        pdf.save(out)
        pdf_bytes = out.getvalue()
    except Exception as e:
        logger.error(f"Error generating PDF for {pdf_req.title}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    pdf_io = io.BytesIO(pdf_bytes)
    filename_root = pdf_req.title.replace(" ", "_") or "export"
    if len(included_titles) > 1:
        filename_root += "_bundle"
    filename = f"{filename_root}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return StreamingResponse(pdf_io, media_type="application/pdf", headers=headers)
