"""
Page routes for WikiWare.
Handles page viewing, editing, and saving operations.
"""

from fastapi import APIRouter, Request, Form, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import markdown
from ..utils.markdown_extensions import InternalLinkExtension
from ..utils.link_processor import process_internal_links
from ..services.page_service import PageService
from ..services.branch_service import BranchService
from ..database import db_instance
from ..utils.validation import is_valid_title
from ..config import TEMPLATE_DIR
from ..middleware.auth_middleware import AuthMiddleware
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, response: Response, branch: str = "main", csrf_protect: CsrfProtect = Depends()):
    """Home page showing list of all pages."""
    # Get current user
    user = await AuthMiddleware.get_current_user(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)
    
    if not db_instance.is_connected:
        return templates.TemplateResponse("home.html", {"request": request, "pages": [], "offline": True, "branch": branch, "user": user, "csrf_token": csrf_token})

    # Get available branches for home page
    branches = await BranchService.get_available_branches()

    # Get pages for the branch
    pages = await PageService.get_pages_by_branch(branch)

    return templates.TemplateResponse("home.html", {
        "request": request,
        "pages": pages,
        "offline": not db_instance.is_connected,
        "branch": branch,
        "branches": branches,
        "user": user,
        "csrf_token": csrf_token
    })


@router.get("/page/{title}", response_class=HTMLResponse)
async def get_page(request: Request, response: Response, title: str, branch: str = "main", csrf_protect: CsrfProtect = Depends()):
    """View a specific page."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        csrf_protect.set_csrf_cookie(signed_token, response)
        
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "user": user, "csrf_token": csrf_token})

        # Get page-specific branches
        branches = await BranchService.get_branches_for_page(title)

        # Get the page
        page = await PageService.get_page(title, branch)

        if not page:
            logger.info(f"Page not found - viewing edit page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False, "branch": branch, "branches": branches, "user": user, "csrf_token": csrf_token})

        # First process internal links with our custom processor
        processed_content = process_internal_links(page["content"])
        # Then render as Markdown (with any remaining Markdown syntax)
        md = markdown.Markdown()
        page["html_content"] = md.convert(processed_content)
        logger.info(f"Page viewed: {title} on branch: {branch}")
        return templates.TemplateResponse("page.html", {"request": request, "page": page, "branch": branch, "offline": False, "branches": branches, "user": user, "csrf_token": csrf_token})
    except Exception as e:
        logger.error(f"Error viewing page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})


@router.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(request: Request, response: Response, title: str, branch: str = "main", csrf_protect: CsrfProtect = Depends()):
    """Edit page form."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        csrf_protect.set_csrf_cookie(signed_token, response)
        
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - editing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "user": user, "csrf_token": csrf_token})

        # Get branches specific to this page
        branches = await BranchService.get_branches_for_page(title)

        # Get existing content
        content = ""
        page = await PageService.get_page(title, branch)
        if page:
            content = page["content"]

        logger.info(f"Page edit accessed: {title} on branch: {branch} by user: {user['username']}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": content, "branch": branch, "offline": not db_instance.is_connected, "branches": branches, "user": user, "csrf_token": csrf_token})
    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error accessing edit page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})


@router.post("/edit/{title}")
async def save_page(request: Request, title: str, content: str = Form(...), author: str = Form("Anonymous"), branch: str = Form("main")):
    """Save page changes."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)
        
        if not db_instance.is_connected:
            logger.error(f"Database not connected - saving page: {title} on branch: {branch}")
            return {"error": "Database not available"}

        # Validate title
        if not is_valid_title(title):
            raise HTTPException(status_code=400, detail="Invalid page title")

        # Use the authenticated user as the author
        author = user["username"]

        # Save the page
        success = await PageService.update_page(title, content, author, branch)

        if success:
            return RedirectResponse(url=f"/page/{title}?branch={branch}&updated=true", status_code=303)
        else:
            return {"error": "Failed to save page"}
    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error saving page {title} on branch {branch}: {str(e)}")
        return {"error": f"Failed to save page: {str(e)}"}
