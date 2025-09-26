"""
Page routes for WikiWare.
Handles page viewing, editing, and saving operations.
"""

import re
from typing import List, Optional
from urllib.parse import quote

import markdown
from markdown.extensions.toc import TocExtension
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...services.user_service import UserService
from ...database import get_users_collection
from ...stats import get_stats
from ...utils.link_processor import process_internal_links
from ...utils.sanitizer import sanitize_html
from ...utils.template_env import get_templates
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()

EDIT_PERMISSION_EVERYBODY = "everybody"
EDIT_PERMISSION_TEN_EDITS = "10_edits"
EDIT_PERMISSION_FIFTY_EDITS = "50_edits"
EDIT_PERMISSION_SELECT_USERS = "select_users"
VALID_EDIT_PERMISSIONS = {
    EDIT_PERMISSION_EVERYBODY,
    EDIT_PERMISSION_TEN_EDITS,
    EDIT_PERMISSION_FIFTY_EDITS,
    EDIT_PERMISSION_SELECT_USERS,
}

templates = get_templates()

def _sanitize_edit_permission(value: Optional[str]) -> str:
    """Return a valid edit permission value, falling back to everybody."""
    if not value:
        return EDIT_PERMISSION_EVERYBODY
    value = value.strip()
    return value if value in VALID_EDIT_PERMISSIONS else EDIT_PERMISSION_EVERYBODY


def _parse_allowed_users(raw: str) -> List[str]:
    """Split a comma-separated allowed users string into a list."""
    if not raw:
        return []
    return [username.strip() for username in raw.split(',') if username.strip()]


def _render_error_page(
    request: Request,
    user: Optional[dict],
    title: str,
    message: str,
    status_code: int = 403,
) -> HTMLResponse:
    """Render a shared error template with the provided message."""
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": title,
            "message": message,
            "user": user,
            "branch": request.query_params.get("branch", "main"),
            "offline": getattr(request.state, "offline", False),
            "csrf_token": getattr(request.state, "csrf_token", ""),
        },
        status_code=status_code,
    )


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


def _transform_toc_tokens(tokens: Optional[List[dict]]) -> List[dict]:
    """Convert markdown toc_tokens into a simplified structure for templates."""
    items: List[dict] = []
    if not tokens:
        return items
    for token in tokens:
        anchor = token.get("id")
        title = (token.get("name") or token.get("title") or token.get("id") or "").strip()
        if not anchor or not title:
            continue
        children = _transform_toc_tokens(token.get("children"))
        items.append(
            {
                "id": anchor,
                "title": title,
                "level": token.get("level", 0),
                "children": children,
            }
        )
    return items


def _count_toc_entries(items: List[dict]) -> int:
    """Return the total number of entries in a nested TOC tree."""
    total = 0
    for item in items:
        total += 1 + _count_toc_entries(item.get("children", []))
    return total


async def _render_markdown_with_toc(content: str) -> tuple[str, List[dict]]:
    """Renders markdown content to HTML and extracts a table of contents."""
    # Process internal links first
    processed_content = await process_internal_links(content)

    # Set up markdown processor with extensions
    md = markdown.Markdown(
        extensions=["tables", TocExtension(permalink=False)]
    )

    # Convert to HTML and sanitize
    html_content = md.convert(processed_content)
    sanitized_html = sanitize_html(html_content)

    # Extract and transform TOC tokens
    toc_items = _transform_toc_tokens(getattr(md, "toc_tokens", []))
    if _count_toc_entries(toc_items) < 2:
        toc_items = []

    return sanitized_html, toc_items


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


async def _can_user_edit_page(user: dict, page_data: Optional[dict]) -> bool:
    """Check if user can edit the page based on permissions."""
    if not page_data:
        return True  # New page, anyone can create

    permission = _sanitize_edit_permission(page_data.get('edit_permission'))
    if permission == EDIT_PERMISSION_EVERYBODY:
        return True
    if permission == EDIT_PERMISSION_TEN_EDITS:
        return user.get('total_edits', 0) >= 10
    if permission == EDIT_PERMISSION_FIFTY_EDITS:
        return user.get('total_edits', 0) >= 50
    if permission == EDIT_PERMISSION_SELECT_USERS:
        allowed_users = page_data.get('allowed_users', [])
        if isinstance(allowed_users, list):
            allowed_usernames = list(allowed_users)
        elif isinstance(allowed_users, str):
            allowed_usernames = _parse_allowed_users(allowed_users)
        else:
            allowed_usernames = []
        return user.get('username') in allowed_usernames
    return False



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
                "toc_items": [],
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
    page["html_content"], toc_items = await _render_markdown_with_toc(page["content"])

    template = templates.TemplateResponse(
        "page.html",
        {
            "request": request,
            "page": page,
            "offline": not db_instance.is_connected,
            "branch": branch,
            "toc_items": toc_items,
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

        # Check for user page redirect: /page/User?branch=username -> /user/username
        if title == "User" and branch != "main":
            redirect_url = request.url_for("user_page", username=branch)
            return RedirectResponse(url=str(redirect_url), status_code=303)

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

        # Process internal links and render as Markdown
        page["html_content"], toc_items = await _render_markdown_with_toc(page["content"])
        logger.info(f"Page viewed: {title} on branch: {branch}")
        template = templates.TemplateResponse(
            "page.html",
            {
                "request": request,
                "page": page,
                "branch": branch,
                "offline": False,
                "branches": branches,
                "toc_items": toc_items,
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
        edit_permission = EDIT_PERMISSION_EVERYBODY
        allowed_users: List[str] = []
        page = await PageService.get_page(title, branch)
        if page:
            content = page["content"]
            edit_permission = _sanitize_edit_permission(page.get("edit_permission"))
            allowed_users_data = page.get("allowed_users", [])
            if isinstance(allowed_users_data, list):
                allowed_users = list(allowed_users_data)
            elif isinstance(allowed_users_data, str):
                allowed_users = _parse_allowed_users(allowed_users_data)
            else:
                allowed_users = []
            # Check if user can edit this page
            if not await _can_user_edit_page(user, page):
                return _render_error_page(
                    request,
                    user,
                    "Access Denied",
                    "You do not have permission to edit this page.",
                    status_code=403,
                )

        # Get all users for the multi-select
        all_users = []
        if db_instance.is_connected:
            users_collection = get_users_collection()
            if users_collection is not None:
                cursor = users_collection.find({}, {"username": 1})
                async for user_doc in cursor:
                    all_users.append(user_doc["username"])
                all_users.sort()

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
                "edit_permission": edit_permission,
                "allowed_users": allowed_users,
                "all_users": all_users,
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
    edit_summary: str = Form(...),
    edit_permission: str = Form("everybody"),
    allowed_users: str = Form(""),
):
    """Save page changes."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if (
            await _is_user_page_title(title)
            and user["username"].casefold() != title.casefold()
        ):
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

        page_data = await PageService.get_page(title, branch)

        if not await _can_user_edit_page(user, page_data):
            return _render_error_page(
                request,
                user,
                "Access Denied",
                "You do not have permission to edit this page.",
                status_code=403,
            )

        is_admin = user.get("is_admin", False)
        edit_permission = _sanitize_edit_permission(edit_permission)

        if is_admin:
            allowed_users_list = _parse_allowed_users(allowed_users)
        else:
            if page_data:
                edit_permission = _sanitize_edit_permission(
                    page_data.get("edit_permission")
                )
                existing_allowed_users = page_data.get("allowed_users", [])
                if isinstance(existing_allowed_users, list):
                    allowed_users_list = list(existing_allowed_users)
                elif isinstance(existing_allowed_users, str):
                    allowed_users_list = _parse_allowed_users(existing_allowed_users)
                else:
                    allowed_users_list = []
            else:
                edit_permission = EDIT_PERMISSION_EVERYBODY
                allowed_users_list = []

        # Save the page
        success = await PageService.update_page(
            title,
            content,
            author,
            branch,
            edit_summary=edit_summary,
            edit_permission=edit_permission,
            allowed_users=allowed_users_list,
        )

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
                logger.warning(
                    f"Attempted redirect with invalid title '{title}' or branch '{branch}'"
                )
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
