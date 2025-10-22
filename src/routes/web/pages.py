"""
Page routes for WikiWare.
Handles page viewing, editing, and saving operations.
"""

import re
from typing import Dict, List, Optional
from urllib.parse import quote

import markdown
from markdown.extensions.toc import TocExtension
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...middleware.rate_limiter import rate_limit
from ...services.analytics_service import AnalyticsService
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...services.settings_service import FeatureFlags
from ...database import get_users_collection
from ...stats import get_stats
from ...utils.link_processor import process_internal_links
from ...utils.navigation_history import (
    HISTORY_COOKIE_MAX_AGE,
    HISTORY_COOKIE_NAME,
    prepare_navigation_context,
    serialize_history,
)
from ...utils.sanitizer import sanitize_html
from ...utils.template_env import get_templates
from ...utils.validation import is_safe_branch_parameter, is_valid_title
from ...utils.markdown_extensions import (
    TableExtensionWrapper,
    ImageFigureExtension,
    SourceExtension,
)
from ...utils.error_utils import render_error_page

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

EDIT_PAGE_RATE_LIMIT = rate_limit(
    "page-edit",
    detail="Too many edit requests. Please wait 1 minute before trying again.",
    use_user_identity=True,
)


def _get_feature_flags(request: Request) -> FeatureFlags:
    """Return feature flags from request state."""
    return request.state.feature_flags


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
    return [username.strip() for username in raw.split(",") if username.strip()]


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
        title = (
            token.get("name") or token.get("title") or token.get("id") or ""
        ).strip()
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


async def _render_markdown_with_toc(content: str) -> tuple[str, List[dict], list]:
    """Renders markdown content to HTML and extracts a table of contents."""
    # Process internal links first
    processed_content = await process_internal_links(content)

    # Set up markdown processor with extensions
    md = markdown.Markdown(
        extensions=[
            TableExtensionWrapper(),
            SourceExtension(),
            ImageFigureExtension(),
            TocExtension(permalink=False),
        ]
    )

    # Convert to HTML and sanitize
    html_content = md.convert(processed_content)
    sanitized_html = sanitize_html(html_content)

    # Extract sources
    sources = getattr(md, "sources", [])

    # Extract and transform TOC tokens
    toc_items = _transform_toc_tokens(getattr(md, "toc_tokens", []))
    if _count_toc_entries(toc_items) < 2:
        toc_items = []

    return sanitized_html, toc_items, sources


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

    permission = _sanitize_edit_permission(page_data.get("edit_permission"))
    if permission == EDIT_PERMISSION_EVERYBODY:
        return True
    if permission == EDIT_PERMISSION_TEN_EDITS:
        return user.get("total_edits", 0) >= 10
    if permission == EDIT_PERMISSION_FIFTY_EDITS:
        return user.get("total_edits", 0) >= 50
    if permission == EDIT_PERMISSION_SELECT_USERS:
        allowed_users = page_data.get("allowed_users", [])
        if isinstance(allowed_users, list):
            allowed_usernames = list(allowed_users)
        elif isinstance(allowed_users, str):
            allowed_usernames = _parse_allowed_users(allowed_users)
        else:
            allowed_usernames = []
        return user.get("username") in allowed_usernames
    return False


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
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

    history_entries, previous_page_context = prepare_navigation_context(
        request, "Home", branch, True
    )

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
                "navigation_previous": previous_page_context,
                "toc_items": [],
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        template.set_cookie(
            HISTORY_COOKIE_NAME,
            serialize_history(history_entries),
            max_age=HISTORY_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
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
            "sources": [],
        }

    # Process internal links and render as Markdown
    page["html_content"], toc_items, sources = await _render_markdown_with_toc(
        page["content"]
    )
    page["sources"] = sources

    await AnalyticsService.record_page_view(request, "Home", branch, user)

    template = templates.TemplateResponse(
        "page.html",
        {
            "request": request,
            "page": page,
            "offline": not db_instance.is_connected,
            "branch": branch,
            "navigation_previous": previous_page_context,
            "toc_items": toc_items,
            "sources": page.get("sources", []),
            "user": user,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    template.set_cookie(
        HISTORY_COOKIE_NAME,
        serialize_history(history_entries),
        max_age=HISTORY_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return template


@router.api_route("/page/{title}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def get_page(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View a specific page."""
    history_entries: List[Dict[str, object]] = []
    previous_page_context: Optional[Dict[str, str]] = None
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        # Ensure CSRF cookie is set on the actual response object we return

        history_entries, previous_page_context = prepare_navigation_context(
            request, title, branch, False
        )

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
                    "navigation_previous": previous_page_context,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            template.set_cookie(
                HISTORY_COOKIE_NAME,
                serialize_history(history_entries),
                max_age=HISTORY_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
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
                    "navigation_previous": previous_page_context,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            template.set_cookie(
                HISTORY_COOKIE_NAME,
                serialize_history(history_entries),
                max_age=HISTORY_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
            return template

        # Process internal links and render as Markdown
        page["html_content"], toc_items, sources = await _render_markdown_with_toc(
            page["content"]
        )
        page["sources"] = sources
        logger.info(f"Page viewed: {title} on branch: {branch}")
        await AnalyticsService.record_page_view(request, title, branch, user)
        template = templates.TemplateResponse(
            "page.html",
            {
                "request": request,
                "page": page,
                "branch": branch,
                "offline": False,
                "branches": branches,
                "toc_items": toc_items,
                "sources": page.get("sources", []),
                "user": user,
                "csrf_token": csrf_token,
                "navigation_previous": previous_page_context,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        template.set_cookie(
            HISTORY_COOKIE_NAME,
            serialize_history(history_entries),
            max_age=HISTORY_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
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
        if history_entries:
            template.set_cookie(
                HISTORY_COOKIE_NAME,
                serialize_history(history_entries),
                max_age=HISTORY_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
        return template


@router.get(
    "/edit/{title}",
    response_class=HTMLResponse,
    dependencies=[Depends(EDIT_PAGE_RATE_LIMIT)],
)
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

        feature_flags = _get_feature_flags(request)
        if not feature_flags.page_editing_enabled and not user.get("is_admin", False):
            return render_error_page(
                request,
                user=user,
                title="Editing Disabled",
                message="Page editing is currently disabled by an administrator.",
                status_code=403,
            )

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
                return render_error_page(
                    request,
                    user=user,
                    title="Access Denied",
                    message="You do not have permission to edit this page.",
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
                "feature_flags": feature_flags,
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


@router.post(
    "/edit/{title}",
    dependencies=[Depends(EDIT_PAGE_RATE_LIMIT)],
)
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

        feature_flags = _get_feature_flags(request)
        if not feature_flags.page_editing_enabled and not user.get("is_admin", False):
            return render_error_page(
                request,
                user=user,
                title="Editing Disabled",
                message="Page editing is currently disabled by an administrator.",
                status_code=403,
            )

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
            return render_error_page(
                request,
                title="Database Error",
                message="The database is currently unavailable. Please try again later.",
                status_code=503,
            )

        # Validate title
        if not is_valid_title(title):
            raise HTTPException(status_code=400, detail="Invalid page title")

        if not is_safe_branch_parameter(branch):
            raise HTTPException(status_code=400, detail="Invalid branch")

        # Use the authenticated user as the author
        author = user["username"]

        page_data = await PageService.get_page(title, branch)
        previous_permission = (
            _sanitize_edit_permission(page_data.get("edit_permission"))
            if page_data
            else EDIT_PERMISSION_EVERYBODY
        )
        previous_allowed_users: List[str] = []
        if page_data:
            existing_allowed_users_data = page_data.get("allowed_users", [])
            if isinstance(existing_allowed_users_data, list):
                previous_allowed_users = [
                    str(username).strip()
                    for username in existing_allowed_users_data
                    if str(username).strip()
                ]
            elif isinstance(existing_allowed_users_data, str):
                previous_allowed_users = _parse_allowed_users(
                    existing_allowed_users_data
                )

        if not await _can_user_edit_page(user, page_data):
            return render_error_page(
                request,
                user=user,
                title="Access Denied",
                message="You do not have permission to edit this page.",
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
                    allowed_users_list = [
                        str(username).strip()
                        for username in existing_allowed_users
                        if str(username).strip()
                    ]
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
            if is_admin:
                normalized_previous = sorted(
                    {username for username in previous_allowed_users}
                )
                normalized_current = sorted({username for username in allowed_users_list})
                permission_changed = edit_permission != previous_permission
                allowed_users_changed = normalized_previous != normalized_current
                became_protected = edit_permission != EDIT_PERMISSION_EVERYBODY
                if became_protected and (permission_changed or allowed_users_changed):
                    logger.info(
                        "Page protection updated for '%s' on branch '%s' by admin %s: mode=%s allowed_users=%s",
                        title,
                        branch,
                        author,
                        edit_permission,
                        normalized_current,
                    )

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


@router.post("/rename/{title}")
async def rename_page(
    request: Request,
    title: str,
    new_title: str = Form(...),
    branch: str = Form("main"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Rename a page, restricted to administrators."""
    try:
        await csrf_protect.validate_csrf(request)

        user = await AuthMiddleware.require_auth(request)
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        raw_new_title = (new_title or "").strip()
        raw_branch = (branch or "main").strip()
        safe_branch = raw_branch if is_safe_branch_parameter(raw_branch) else "main"
        normalized_title = raw_new_title.strip()

        edit_url = request.url_for("edit_page", title=title)
        if safe_branch != "main":
            edit_url = edit_url.include_query_params(branch=safe_branch)

        if title == "Home":
            edit_url = edit_url.include_query_params(rename_status="protected")
            return RedirectResponse(url=str(edit_url), status_code=303)

        if not normalized_title:
            edit_url = edit_url.include_query_params(rename_status="missing_title")
            return RedirectResponse(url=str(edit_url), status_code=303)

        if normalized_title == title:
            edit_url = edit_url.include_query_params(rename_status="unchanged")
            return RedirectResponse(url=str(edit_url), status_code=303)

        if not is_valid_title(normalized_title):
            edit_url = edit_url.include_query_params(rename_status="invalid")
            return RedirectResponse(url=str(edit_url), status_code=303)

        if await _is_user_page_title(title):
            edit_url = edit_url.include_query_params(rename_status="user_page")
            return RedirectResponse(url=str(edit_url), status_code=303)

        success, reason = await PageService.rename_page(title, normalized_title)
        if not success:
            status = reason or "error"
            edit_url = edit_url.include_query_params(rename_status=status)
            return RedirectResponse(url=str(edit_url), status_code=303)

        new_edit_url = request.url_for("edit_page", title=normalized_title)
        if safe_branch != "main":
            new_edit_url = new_edit_url.include_query_params(branch=safe_branch)
        new_edit_url = new_edit_url.include_query_params(rename_status="success")
        return RedirectResponse(url=str(new_edit_url), status_code=303)

    except CsrfProtectError as e:
        logger.warning(
            "CSRF validation failed while renaming page %s: %s",
            title,
            str(e),
        )
        fallback = request.url_for("edit_page", title=title)
        fallback = fallback.include_query_params(rename_status="csrf")
        return RedirectResponse(url=str(fallback), status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error renaming page {title}: {str(e)}")
        fallback = request.url_for("edit_page", title=title)
        fallback = fallback.include_query_params(rename_status="error")
        return RedirectResponse(url=str(fallback), status_code=303)


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
            return render_error_page(
                request,
                title="Database Error",
                message="The database is currently unavailable. Please try again later.",
                status_code=503,
            )

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
            return render_error_page(
                request,
                title="Database Error",
                message="The database is currently unavailable. Please try again later.",
                status_code=503,
            )

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
