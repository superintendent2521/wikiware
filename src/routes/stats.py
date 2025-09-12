"""
Stats routes for WikiWare.
Handles statistics display.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ..services.branch_service import BranchService
from ..database import db_instance
from ..config import TEMPLATE_DIR
from ..stats import get_stats
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, branch: str = "main"):
    """Display wiki statistics page."""
    try:
        if not db_instance.is_connected:
            logger.warning("Database not connected - viewing stats")
            return templates.TemplateResponse("stats.html", {"request": request, "offline": True, "branch": branch})

        # Get available branches
        branches = await BranchService.get_available_branches()

        # Get statistics
        stats = await get_stats()

        logger.info("Stats page viewed")
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "total_edits": stats["total_edits"],
            "total_characters": stats["total_characters"],
            "total_pages": stats["total_pages"],
            "total_images": stats["total_images"],
            "last_updated": stats["last_updated"],
            "offline": False,
            "branch": branch,
            "branches": branches
        })
    except Exception as e:
        logger.error(f"Error viewing stats page: {str(e)}")
        return templates.TemplateResponse("stats.html", {"request": request, "offline": True, "branch": branch})
