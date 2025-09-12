"""
Search routes for WikiWare.
Handles search functionality.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ..services.page_service import PageService
from ..services.branch_service import BranchService
from ..database import db_instance
from ..config import TEMPLATE_DIR
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", branch: str = "main"):
    """Search pages by query."""
    try:
        if not db_instance.is_connected:
            logger.warning("Database not connected - search attempted")
            return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

        # Get available branches
        branches = await BranchService.get_available_branches()

        pages = []
        if q:
            pages = await PageService.search_pages(q, branch)
        else:
            logger.info("Search accessed without query")

        return templates.TemplateResponse("search.html", {
            "request": request,
            "pages": pages,
            "query": q,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "branches": branches
        })
    except Exception as e:
        logger.error(f"Error during search '{q}' on branch '{branch}': {str(e)}")
        return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})
