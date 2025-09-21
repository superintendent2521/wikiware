"""
Template processor for dynamic content rendering in wiki pages.
Performs safe, limited placeholder substitution (no Jinja execution).
"""

import re
from loguru import logger
from ..stats import get_stats
from ..database import db_instance


_SIMPLE_TOKEN_RE = re.compile(
    r"\{\{\s*global\.(edits|pages|characters|images|last_updated)\s*\}\}"
)
_COLOR_TOKEN_RE = re.compile(
    r"\{\{\s*global\.color\.(red|green|blue|purple|pink|orange|yellow|gray|cyan)\s*\}\}"
)


async def render_template_content(content: str, request: dict = None) -> str:
    """
    Render limited placeholders in page content.
    Supports tokens like {{ global.edits }} and {{ global.color.red }}.
    """
    if not content:
        return content

    if not db_instance.is_connected:
        # If DB is down, return content unchanged (no stats available)
        return content

    try:
        stats = await get_stats()
        values = {
            "edits": str(stats.get("total_edits", "")),
            "pages": str(stats.get("total_pages", "")),
            "characters": str(stats.get("total_characters", "")),
            "images": str(stats.get("total_images", "")),
            "last_updated": str(stats.get("last_updated", "")),
        }

        color_spans = {
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

        def _replace_simple(m: re.Match) -> str:
            key = m.group(1)
            return values.get(key, "")

        def _replace_color(m: re.Match) -> str:
            key = m.group(1)
            return color_spans.get(key, "")

        content = _SIMPLE_TOKEN_RE.sub(_replace_simple, content)
        content = _COLOR_TOKEN_RE.sub(_replace_color, content)
        return content

    except Exception as e:
        logger.error(f"Error rendering placeholders in page content: {str(e)}")
        return content
