"""
Page routes for WikiWare.
Handles page viewing, editing, and saving operations.
"""

from fastapi import APIRouter, Request, Form, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import markdown
import re
from ..utils.link_processor import process_internal_links
from urllib.parse import quote
from ..utils.sanitizer import sanitize_html
from ..services.page_service import PageService
from ..services.branch_service import BranchService
from ..database import db_instance
from ..utils.validation import is_valid_title, is_safe_branch_parameter
from ..config import TEMPLATE_DIR
from ..middleware.auth_middleware import AuthMiddleware
from ..stats import get_stats
from ..services.user_service import UserService
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _build_page_redirect_url(request: Request, title: str, branch: str) -> str:
    """Build an internal URL for the page route with proper encoding."""
    target_url = request.url_for("get_page", title=title)
    if branch != "main":
        target_url = target_url.include_query_params(branch=branch)
    return str(target_url)


def _build_user_page_redirect_url(request: Request, username: str, branch: str) -> str:
    """Build the user page URL while keeping branch parameters safe."""
    target_url = request.url_for("user_page", username=username)
    if branch != "main" and is_safe_branch_parameter(branch):
        target_url = target_url.include_query_params(branch=branch)
    return str(target_url)


async def _is_user_page_title(title: str) -> bool:
    """Return True if the title matches an existing user's personal page."""
    if not title or not db_instance.is_connected:
        return False

    user_doc = await UserService.get_user_by_username(title)
    if user_doc:
        return True

    users_collection = db_instance.get_collection("users")
    if users_collection is None:
        return False

    regex = {"$regex": f"^{re.escape(title)}$", "$options": "i"}
    user_doc = await users_collection.find_one({"username": regex})
    return user_doc is not None

@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    response: Response,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Home page showing the hardcoded 'Home' page."""
    # Get current user
    user = await AuthMiddleware.get_current_user(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    if not db_instance.is_connected:
        template = templates.TemplateResponse(
            "page.html",
            {
                "request": request,
                "page": {
                    "title": "Home",
                    "content": "",
                    "author": "",
                    "updated_at": "",
                    "branch": branch,
                },
                "offline": True,
                "branch": branch,
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    # Get the hardcoded 'Home' page
    page = await PageService.get_page("Home", branch)

    if not page:
        # If Home page doesn't exist, create a placeholder
        page = {
            "title": "Home",
            "content": "",
            "author": "",
            "updated_at": "",
            "branch": branch,
        }

    # Process internal links and render as Markdown
    processed_content = await process_internal_links(page["content"])
    md = markdown.Markdown(extensions=["tables"])
    page["html_content"] = sanitize_html(md.convert(processed_content))

    template = templates.TemplateResponse(
        "page.html",
        {
            "request": request,
            "page": page,
            "offline": not db_instance.is_connected,
            "branch": branch,
            "user": user,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/page/{title}", response_class=HTMLResponse)
async def get_page(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View a specific page."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        # Ensure CSRF cookie is set on the actual response object we return

        if not db_instance.is_connected:
            logger.warning(
                f"Database not connected - viewing page: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "offline": True,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Get page-specific branches
        branches = await BranchService.get_branches_for_page(title)

        # Get the page
        page = await PageService.get_page(title, branch)

        if not page:
            logger.info(
                f"Page not found - viewing edit page: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "offline": False,
                    "branch": branch,
                    "branches": branches,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # First process internal links with our custom processor
        processed_content = await process_internal_links(page["content"])
        # Then render as Markdown (with any remaining Markdown syntax)
        md = markdown.Markdown(extensions=["tables"])
        page["html_content"] = sanitize_html(md.convert(processed_content))
        logger.info(f"Page viewed: {title} on branch: {branch}")
        template = templates.TemplateResponse(
            "page.html",
            {
                "request": request,
                "page": page,
                "branch": branch,
                "offline": False,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(f"Error viewing page {title} on branch {branch}: {str(e)}")
        # Safely regenerate CSRF tokens for the error response
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""

        # Get global stats for error fallback (edit.html)
        if db_instance.is_connected:
            stats = await get_stats()
            global_stats = {
                "edits": stats["total_edits"],
                "pages": stats["total_pages"],
                "characters": stats["total_characters"],
                "images": stats["total_images"],
                "last_updated": stats["last_updated"],
            }
        else:
            global_stats = {
                "edits": 0,
                "pages": 0,
                "characters": 0,
                "images": 0,
                "last_updated": None,
            }

        template = templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": title,
                "content": "",
                "offline": True,
                "csrf_token": csrf_token_e,
                "global": global_stats,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Edit page form."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if await _is_user_page_title(title) and user["username"].casefold() != title.casefold():
            logger.warning(
                f"User {user['username']} attempted to edit personal page {title} via generic editor"
            )
            redirect_url = _build_user_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        # Ensure CSRF cookie is set on the actual response object we return

        if not db_instance.is_connected:
            logger.warning(
                f"Database not connected - editing page: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "offline": True,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Get branches specific to this page
        branches = await BranchService.get_branches_for_page(title)

        # Get existing content
        content = ""
        page = await PageService.get_page(title, branch)
        if page:
            content = page["content"]

        logger.info(
            f"Page edit accessed: {title} on branch: {branch} by user: {user['username']}"
        )

        # Get global stats for display in editor
        if db_instance.is_connected:
            stats = await get_stats()
            global_stats = {
                "edits": stats["total_edits"],
                "pages": stats["total_pages"],
                "characters": stats["total_characters"],
                "images": stats["total_images"],
                "last_updated": stats["last_updated"],
            }
        else:
            global_stats = {
                "edits": 0,
                "pages": 0,
                "characters": 0,
                "images": 0,
                "last_updated": None,
            }

        template = templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": title,
                "content": content,
                "branch": branch,
                "offline": not db_instance.is_connected,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
                "global": global_stats,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error accessing edit page {title} on branch {branch}: {str(e)}")
        # Safely regenerate CSRF tokens for the error response
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": title,
                "content": "",
                "offline": True,
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.post("/edit/{title}")
async def save_page(
    request: Request,
    title: str,
    content: str = Form(...),
    author: str = Form("Anonymous"),
    branch: str = Form("main"),
):
    """Save page changes."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if await _is_user_page_title(title) and user["username"].casefold() != title.casefold():
            logger.warning(
                f"User {user['username']} attempted to save personal page {title} via generic editor"
            )
            redirect_url = _build_user_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - saving page: {title} on branch: {branch}"
            )
            return {"error": "Database not available"}

        # Validate title
        if not is_valid_title(title):
            raise HTTPException(status_code=400, detail="Invalid page title")

        if not is_safe_branch_parameter(branch):
            raise HTTPException(status_code=400, detail="Invalid branch")

        # Use the authenticated user as the author
        author = user["username"]

        # Save the page
        success = await PageService.update_page(title, content, author, branch)

        if success:
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)
        else:
            return {"error": "Failed to save page"}
    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error saving page {title} on branch {branch}: {str(e)}")
        return {"error": "Failed to save page"}


@router.post("/delete/{title}")
async def delete_page(
    request: Request, title: str, csrf_protect: CsrfProtect = Depends()
):
    """Delete a page (all branches)."""
    try:
        # Validate CSRF token (reads token from body per config and cookie)
        # Add extra diagnostics in logs to help track issues
        form_data = await request.form()
        csrf_token = form_data.get("csrf_token")
        logger.debug(
            f"Delete page '{title}' csrf_token in form present={bool(csrf_token)}; cookies keys={list(request.cookies.keys())}"
        )
        logger.debug(
            f"CSRF cookie value present={bool(request.cookies.get('fastapi-csrf-token'))}"
        )
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated and is an admin
        user = await AuthMiddleware.require_auth(request)
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        if not db_instance.is_connected:
            logger.error(f"Database not connected - cannot delete page: {title}")
            return {"error": "Database not available"}

        # Use PageService to delete the page (all branches)
        success = await PageService.delete_page(title)

        if success:
            logger.info(
                f"Page deleted (all branches): {title} by admin {user['username']}"
            )
            return RedirectResponse(url="/", status_code=303)
        else:
            logger.warning(f"Page not found for deletion: {title}")
            return {"error": "Page not found"}

    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error deleting page {title}: {str(e)}")
        return {"error": "Failed to delete page"}


@router.post("/delete-branch/{title}")
async def delete_branch(
    request: Request,
    title: str,
    branch: str = Form("main"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Delete a specific branch from a page."""
    try:
        # Validate CSRF token (reads token from body per config and cookie)
        # Add extra diagnostics in logs to help track issues
        form_data = await request.form()
        csrf_token = form_data.get("csrf_token")
        logger.debug(
            f"Delete branch '{branch}' from page '{title}' csrf_token in form present={bool(csrf_token)}; cookies keys={list(request.cookies.keys())}"
        )
        logger.debug(
            f"CSRF cookie value present={bool(request.cookies.get('fastapi-csrf-token'))}"
        )
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated and is an admin
        user = await AuthMiddleware.require_auth(request)
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - cannot delete branch {branch} from page {title}"
            )
            return {"error": "Database not available"}

        # Use PageService to delete the branch from the page
        success = await PageService.delete_branch(title, branch)

        if success:
            logger.info(
                f"Branch deleted from page: {branch} from {title} by admin {user['username']}"
            )
            # Validate before using in redirect URL
            if not is_valid_title(title) or not is_safe_branch_parameter(branch):
                logger.warning(f"Attempted redirect with invalid title '{title}' or branch '{branch}'")
                return RedirectResponse(url="/", status_code=303)
            safe_title = quote(title, safe="")
            safe_branch = quote(branch, safe="")
            return RedirectResponse(
                url=f"/page/{safe_title}?branch={safe_branch}", status_code=303
            )
        else:
            logger.warning(f"Branch not found for deletion: {branch} from page {title}")
            return {"error": "Branch not found"}

    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error deleting branch {branch} from page {title}: {str(e)}")
        return {"error": "Failed to delete branch"}
