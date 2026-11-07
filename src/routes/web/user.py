"""
Page routes for WikiWare.
Handles user-specific page viewing, editing, and saving operations.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.page_service import PageService
from ...services.page_errors import PageServiceError, PageUnavailableError
from ...services.page_permissions import get_page_edit_denial, normalize_allowed_users
from ...services.user_service import UserService
from ...services.settings_service import FeatureFlags, SettingsService
from ...services.render_service import render_markdown, render_sources_html
from ...utils.template_env import get_templates
from ...utils.validation import is_safe_branch_parameter, is_valid_title
from ...utils.editor_response import save_succeeded, save_failed

router = APIRouter()

templates = get_templates()


async def _get_feature_flags(request: Request) -> FeatureFlags:
    """Return feature flags from request state or via settings service."""
    feature_flags = getattr(request.state, "feature_flags", None)
    if feature_flags is None:
        feature_flags = await SettingsService.get_feature_flags()
        request.state.feature_flags = feature_flags
    return feature_flags


def _render_error_page(
    request: Request,
    user: dict,
    csrf_token: str,
    *,
    branch: str,
    message: str,
    status_code: int = 403,
    title: str = "Editing Disabled",
):
    """Render a standardized error template for user page operations."""
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": title,
            "message": message,
            "user": user,
            "branch": branch,
            "csrf_token": csrf_token,
            "offline": not db_instance.is_connected,
        },
        status_code=status_code,
    )


def _build_user_page_redirect_url(request: Request, username: str, branch: str) -> str:
    """Build an internal URL for the user page route with optional branch."""
    target_url = request.url_for("user_page", username=username)
    if branch != "main":
        target_url = target_url.include_query_params(branch=branch)
    return str(target_url)


@router.get("/favorites", response_class=HTMLResponse)
async def favorites_page(
    request: Request,
    response: Response,
    csrf_protect: CsrfProtect = Depends(),
):
    """Display the authenticated user's favorite pages."""
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    offline = not db_instance.is_connected
    favorites = []
    load_error = None

    if offline:
        load_error = "Favorites are unavailable while the database is offline."
    else:
        favorites_data = await UserService.list_favorites(user["username"])
        if favorites_data is None:
            load_error = (
                "We couldn't load your favorites right now. Please try again later."
            )
        else:
            for entry in favorites_data:
                title = entry.get("title")
                branch = (entry.get("branch") or "main").strip() or "main"
                if not title:
                    continue

                try:
                    page_url = request.url_for("get_page", title=title)
                except Exception:
                    logger.warning(f"Unable to build page URL for favorite '{title}'")
                    continue

                if branch != "main" and is_safe_branch_parameter(branch):
                    page_url = page_url.include_query_params(branch=branch)

                favorites.append(
                    {
                        "title": title,
                        "branch": branch,
                        "url": str(page_url),
                    }
                )

    template = templates.TemplateResponse(
        "favorites.html",
        {
            "request": request,
            "user": user,
            "favorites": favorites,
            "csrf_token": csrf_token,
            "offline": offline,
            "load_error": load_error,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/user/{username}", response_class=HTMLResponse)
async def user_page(
    request: Request,
    response: Response,
    username: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View a user's personal page."""
    current_user = await AuthMiddleware.get_current_user(request)
    feature_flags = await _get_feature_flags(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    if not db_instance.is_connected:
        template = templates.TemplateResponse(
            "user.html",
            {
                "request": request,
                "username": username,
                "content": "",
                "offline": True,
                "user": current_user,
                "is_owner": False,
                "csrf_token": csrf_token,
                "is_user_page": True,
                "feature_flags": feature_flags,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    # Get the user's page
    page = await PageService.get_page(username, branch)

    if not page:
        # If user page doesn't exist, create a placeholder
        page = {
            "title": username,
            "content": f"# Welcome to {username}'s page\n\nThis is your personal page. Edit it to introduce yourself!",
            "author": "",
            "updated_at": "",
            "branch": branch,
        }

    # Process internal links and render as Markdown
    rendered, _, sources = await render_markdown(page["content"])
    page["html_content"] = rendered + render_sources_html(sources)

    # Check if current user is the owner of this page
    is_owner = current_user and current_user["username"] == username

    # Get user statistics
    user_stats = None
    user_doc = await UserService.get_user_by_username(username)
    if user_doc:
        user_stats = {
            "total_edits": user_doc.get("total_edits", 0),
            "page_edits": user_doc.get("page_edits", {}),
        }

    template = templates.TemplateResponse(
        "user.html",
        {
            "request": request,
            "username": username,
            "page": page,
            "offline": not db_instance.is_connected,
            "branch": branch,
            "user": current_user,
            "is_owner": is_owner,
            "csrf_token": csrf_token,
            "user_stats": user_stats,
            "feature_flags": feature_flags,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/user/{username}/edit", response_class=HTMLResponse)
async def edit_user_page(
    request: Request,
    response: Response,
    username: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Edit a user's personal page."""
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    feature_flags = await _get_feature_flags(request)
    if not feature_flags.page_editing_enabled and not user.get("is_admin", False):
        template = _render_error_page(
            request,
            user,
            csrf_token,
            branch=branch,
            message="Page editing is currently disabled by an administrator.",
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    if not db_instance.is_connected:
        template = templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": username,
                "content": "",
                "offline": True,
                "user": user,
                "csrf_token": csrf_token,
                "is_user_page": True,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    # Only allow editing own page
    if user["username"] != username:
        raise HTTPException(
            status_code=403, detail="You can only edit your own user page"
        )

    # Get existing content
    content = ""
    page = await PageService.get_page(username, branch)
    denial = await get_page_edit_denial(
        user, username, page, editing_enabled=feature_flags.page_editing_enabled
    )
    if denial:
        template = _render_error_page(
            request, user, csrf_token, branch=branch, message=denial,
            title="Access Denied",
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    if page:
        content = page["content"]

    template = templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "title": username,
            "content": content,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "user": user,
            "csrf_token": csrf_token,
            "is_user_page": True,
            "feature_flags": feature_flags,
            "expected_version": page["version_token"] if page else "new",
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.post("/user/{username}/edit")
async def save_user_page(
    request: Request,
    username: str,
    content: str = Form(""),
    branch: str = Form("main"),
    edit_summary: str = Form(...),
    expected_version: str = Form(...),
    csrf_protect: CsrfProtect = Depends(),
):
    """Save user page changes."""
    try:
        await csrf_protect.validate_csrf(request)
        user = await AuthMiddleware.require_auth(request)

        feature_flags = await _get_feature_flags(request)
        if not feature_flags.page_editing_enabled and not user.get("is_admin", False):
            return save_failed(request, "Page editing is currently disabled by an administrator.", 403, content)

        if not db_instance.is_connected:
            raise PageUnavailableError()

        # Only allow editing own page
        if user["username"] != username:
            raise HTTPException(
                status_code=403, detail="You can only edit your own user page"
            )

        # Validate title (username) - must be safe for path inclusion
        import re

        if not is_valid_title(username) or not re.match(r"^[a-zA-Z0-9_-]+$", username):
            raise HTTPException(status_code=400, detail="Invalid username")

        if not is_safe_branch_parameter(branch):
            logger.warning(
                f"Invalid branch '{branch}' while saving user page for {username}, defaulting to main"
            )
            branch = "main"

        # Use the authenticated user as the author
        author = user["username"]

        page = await PageService.get_page(username, branch)
        denial = await get_page_edit_denial(
            user, username, page, editing_enabled=feature_flags.page_editing_enabled
        )
        if denial:
            raise HTTPException(status_code=403, detail=denial)

        # Save the page
        success = await PageService.update_page(
            username, content, author, branch, edit_summary=edit_summary,
            edit_permission=page.get("edit_permission", "everybody") if page else "everybody",
            allowed_users=normalize_allowed_users(page.get("allowed_users")) if page else [],
            expected_version=expected_version,
            actor=user,
        )

        if success:
            redirect_url = _build_user_page_redirect_url(request, username, branch)
            return save_succeeded(request, redirect_url)
        else:
            raise PageUnavailableError()
    except PageServiceError as exc:
        return save_failed(request, str(exc), exc.status_code, content)
    except CsrfProtectError as exc:
        return save_failed(request, exc.message, exc.status_code, content)
    except HTTPException as exc:
        if exc.status_code == 401 and "application/json" not in request.headers.get("accept", ""):
            return RedirectResponse(url="/login", status_code=303)
        return save_failed(request, str(exc.detail), exc.status_code, content)
    except Exception as e:
        logger.error(f"Error saving user page {username} on branch {branch}: {str(e)}")
        return save_failed(request, "The page could not be saved. Please try again.", 503, content)
