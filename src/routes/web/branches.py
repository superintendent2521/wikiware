"""
Branch routes for WikiWare.
Handles branch management operations (web interface).
"""

from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from ...database import db_instance
from ...utils.template_env import get_templates
from ...utils.validation import sanitize_referer_url

router = APIRouter()

templates = get_templates()


@router.get("/branches/{title}", response_class=HTMLResponse)
async def list_branches(request: Request, title: str, branch: str = "main"):
    """List all branches for a page."""
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - listing branches for: {title}")
            return templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "offline": True,
                    "branch": branch,
                },
            )

        from ...services.branch_service import BranchService
        branches = await BranchService.get_branches_for_page(title)

        logger.info(f"Branches listed for page: {title}")
        return templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": title,
                "content": "",
                "branches": branches,
                "offline": not db_instance.is_connected,
                "branch": branch,
            },
        )
    except Exception as e:
        logger.error(f"Error listing branches for {title}: {str(e)}")
        return templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": title,
                "content": "",
                "offline": True,
                "branch": branch,
            },
        )
