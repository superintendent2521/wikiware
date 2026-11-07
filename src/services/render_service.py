"""One Markdown pipeline for pages, history, previews, and PDF exports."""

import html
import re

import markdown
from markdown.extensions import Extension
from markdown.extensions.toc import TocExtension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString

from ..stats import get_public_stats
from ..utils.markdown_extensions import (
    ImageFigureExtension, InternalLinkExtension, SourceExtension, TableExtensionWrapper,
)
from ..utils.sanitizer import sanitize_html

_STAT_PATTERN = r"\{\{\s*global\.(edits|pages|characters|images|last_updated)\s*\}\}"
_STAT_KEYS = {
    "edits": "total_edits", "pages": "total_pages", "characters": "total_characters",
    "images": "total_images", "last_updated": "last_updated",
}


class _StatsProcessor(InlineProcessor):
    def __init__(self, values):
        super().__init__(_STAT_PATTERN)
        self.values = values

    def handleMatch(self, match, data):
        value = self.values.get(_STAT_KEYS[match.group(1)], "")
        return AtomicString("" if value is None else str(value)), match.start(), match.end()


class _StatsExtension(Extension):
    def __init__(self, values):
        super().__init__()
        self.values = values

    def extendMarkdown(self, md):
        md.inlinePatterns.register(_StatsProcessor(self.values), "global_stats", 165)


def _toc_items(tokens):
    return [
        {"id": item["id"], "title": item["name"], "level": item["level"],
         "children": _toc_items(item.get("children", []))}
        for item in tokens if item.get("id")
    ]


async def render_markdown(content: str) -> tuple[str, list[dict], list[dict]]:
    """Render authored Markdown without modifying its source or executing templates."""
    stats = await get_public_stats() if re.search(_STAT_PATTERN, content) else {}
    md = markdown.Markdown(extensions=[
        "fenced_code", "sane_lists", InternalLinkExtension(), TableExtensionWrapper(),
        SourceExtension(), ImageFigureExtension(), TocExtension(permalink=False),
        _StatsExtension(stats),
    ])
    rendered = sanitize_html(md.convert(content))
    toc = _toc_items(md.toc_tokens)
    if sum(1 for _ in md.toc_tokens) < 2 and not any(item["children"] for item in toc):
        toc = []
    return rendered, toc, getattr(md, "sources", [])


def render_sources_html(sources: list[dict]) -> str:
    """Render validated source metadata consistently outside the main article."""
    if not sources:
        return ""
    items = "".join(
        f'<li id="source-{source["id"]}"><a href="{html.escape(source["url"], quote=True)}" '
        f'rel="noopener">{html.escape(source["title"])}</a></li>'
        for source in sources
    )
    return f'<section class="page-sources"><h2>Sources</h2><ol>{items}</ol></section>'
