"""
Template processor for dynamic content rendering in wiki pages.
Handles Jinja2 template variable substitution in page content.
"""

from jinja2 import Template
from ..stats import get_stats
from ..database import db_instance
from loguru import logger

async def render_template_content(content: str, request: dict = None) -> str:
    """
    Render Jinja2 template variables in page content.
    Supports variables like {{ global.edits }}, {{ global.pages }}, etc.
    
    Args:
        content (str): Raw page content (Markdown)
        request (dict): Optional request context (used for user, branch, etc.)
    
    Returns:
        str: Content with template variables rendered
    """
    if not db_instance.is_connected:
        # If DB is down, return content unchanged (no stats available)
        return content

    try:
        # Get global stats
        stats = await get_stats()
        global_context = {
            "edits": stats["total_edits"],
            "pages": stats["total_pages"],
            "characters": stats["total_characters"],
            "images": stats["total_images"],
            "last_updated": stats["last_updated"]
        }

        # Create template context
        context = {
            "global": global_context,
            "request": request or {}
        }

        # Render template
        template = Template(content)
        rendered_content = template.render(context)
        return rendered_content

    except Exception as e:
        logger.error(f"Error rendering template in page content: {str(e)}")
        # Return original content if rendering fails (fail-safe)
        return content
