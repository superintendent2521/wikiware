"""
Page routes for WikiWare.
Handles page viewing, editing, and saving operations.
"""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import markdown
from ..services.page_service import PageService
from ..services.branch_service import BranchService
from ..database import db_instance
from ..utils.validation import is_valid_title
from ..config import TEMPLATE_DIR
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, branch: str = "main"):
    """Home page showing list of all pages."""
    if not db_instance.is_connected:
        return templates.TemplateResponse("home.html", {"request": request, "pages": [], "offline": True, "branch": branch})

    # Get available branches
    branches = await BranchService.get_available_branches()

    # Get pages for the branch
    pages = await PageService.get_pages_by_branch(branch)

    return templates.TemplateResponse("home.html", {
        "request": request,
        "pages": pages,
        "offline": not db_instance.is_connected,
        "branch": branch,
        "branches": branches
    })


@router.get("/page/{title}", response_class=HTMLResponse)
async def get_page(request: Request, title: str, branch: str = "main"):
    """View a specific page."""
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        # Get page-specific branches
        branches = await BranchService.get_branches_for_page(title)

        # Get the page
        page = await PageService.get_page(title, branch)

        if not page:
            logger.info(f"Page not found - viewing edit page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False, "branch": branch, "branches": branches})

        page["html_content"] = markdown.markdown(page["content"])
        logger.info(f"Page viewed: {title} on branch: {branch}")
        return templates.TemplateResponse("page.html", {"request": request, "page": page, "branch": branch, "offline": False, "branches": branches})
    except Exception as e:
        logger.error(f"Error viewing page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})


@router.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(request: Request, title: str, branch: str = "main"):
    """Edit page form."""
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - editing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        # Get available branches
        branches = await BranchService.get_available_branches()

        # Get existing content
        content = ""
        page = await PageService.get_page(title, branch)
        if page:
            content = page["content"]

        logger.info(f"Page edit accessed: {title} on branch: {branch}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": content, "branch": branch, "offline": not db_instance.is_connected, "branches": branches})
    except Exception as e:
        logger.error(f"Error accessing edit page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})


@router.post("/edit/{title}")
async def save_page(title: str, content: str = Form(...), author: str = Form("Anonymous"), branch: str = Form("main")):
    """Save page changes."""
    try:
        if not db_instance.is_connected:
            logger.error(f"Database not connected - saving page: {title} on branch: {branch}")
            return {"error": "Database not available"}

        # Validate title
        if not is_valid_title(title):
            raise HTTPException(status_code=400, detail="Invalid page title")

        # Save the page
        success = await PageService.update_page(title, content, author, branch)

        if success:
            return RedirectResponse(url=f"/page/{title}?branch={branch}&updated=true", status_code=303)
        else:
            return {"error": "Failed to save page"}
    except Exception as e:
        logger.error(f"Error saving page {title} on branch {branch}: {str(e)}")
        return {"error": f"Failed to save page: {str(e)}"}
