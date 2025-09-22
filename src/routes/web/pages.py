"""
Page routes for WikiWare.
Handles page viewing operations (web interface).
"""

import re
from urllib.parse import quote

import markdown
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...stats import get_stats
from ...utils.link_processor import process_internal_links
from ...utils.sanitizer import sanitize_html
from ...utils.template_env import get_templates
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()

templates = get_templates()


async def _is_user_page_title(title: str) -> bool:
    """Return True if the title matches an existing user's personal page."""
    if not title or not db_instance.is_connected:
        return False
    users_collection = db_instance.get_collection("users")
    if users_collection is None:
        return False
    # Perform a single case-insensitive query for the username.
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

        if (
            await _is_user_page_title(title)
            and user["username"].casefold() != title.casefold()
        ):
            logger.warning(
                f"User {user['username']} attempted to edit personal page {title} via generic editor"
            )
            redirect_url = request.url_for("user_page", username=title)
            if branch != "main" and is_safe_branch_parameter(branch):
                redirect_url = redirect_url.include_query_params(branch=branch)
            return RedirectResponse(url=str(redirect_url), status_code=303)

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
