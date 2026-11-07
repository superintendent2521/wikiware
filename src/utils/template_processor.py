"""
Template processor for dynamic content rendering in wiki pages.
Performs safe, limited placeholder substitution (no Jinja execution).
"""

import re
from loguru import logger
from ..stats import get_public_stats
from ..database import db_instance


_SIMPLE_TOKEN_RE = re.compile(
    r"\{\{\s*global\.(edits|pages|characters|images|last_updated)\s*\}\}"
)
_COLOR_TOKEN_RE = re.compile(
    r"\{\{\s*global\.color\.(red|green|blue|purple|pink|orange|yellow|gray|cyan)\s*\}\}"
)
_STAT_KEYS = {
    "edits": "total_edits",
    "pages": "total_pages",
    "characters": "total_characters",
    "images": "total_images",
    "last_updated": "last_updated",
}
_COLOR_SPANS = {
    "red": "<span class='color-red'></span>",
    "green": "<span class='color-green'></span>",
    "blue": "<span class='color-blue'></span>",
    "purple": "<span class='color-purple'></span>",
    "pink": "<span class='color-pink'></span>",
    "orange": "<span class='color-orange'></span>",
    "yellow": "<span class='color-yellow'></span>",
    "gray": "<span class='color-gray'></span>",
    "cyan": "<span class='color-cyan'></span>",
}


async def render_template_content(content: str, request: dict = None) -> str:
    """
    Render limited placeholders in page content.
    Supports tokens like {{ global.edits }} and {{ global.color.red }}.
    """
    if not content or not (_SIMPLE_TOKEN_RE.search(content) or _COLOR_TOKEN_RE.search(content)):
        return content

    if not db_instance.is_connected:
        # If DB is down, return content unchanged (no stats available)
        return content

    try:
        stats = await get_public_stats() if _SIMPLE_TOKEN_RE.search(content) else {}
        values = {
            token: str(stats.get(stats_key, ""))
            for token, stats_key in _STAT_KEYS.items()
        }

        def _replace_simple(m: re.Match) -> str:
            key = m.group(1)
            return values.get(key, "")

        def _replace_color(m: re.Match) -> str:
            key = m.group(1)
            return _COLOR_SPANS.get(key, "")

        content = _SIMPLE_TOKEN_RE.sub(_replace_simple, content)
        content = _COLOR_TOKEN_RE.sub(_replace_color, content)
        return content

    except Exception:
        logger.exception("Error rendering placeholders in page content")
        return content
