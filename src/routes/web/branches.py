"""
Branch routes for WikiWare.
Handles branch management operations.
"""

from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.branch_service import BranchService
from ...services.settings_service import SettingsService
from ...utils.template_env import get_templates
from ...utils.validation import (
    is_safe_branch_parameter,
    is_valid_branch_name,
    is_valid_title,
)

router = APIRouter()

templates = get_templates()


def _build_page_redirect_url(request: Request, title: str, branch: str) -> str:
    """Construct a safe internal URL for the page view."""
    safe_branch = branch if is_safe_branch_parameter(branch) else "main"
    target_url = request.url_for("get_page", title=title)
    if safe_branch != "main":
        target_url = target_url.include_query_params(branch=safe_branch)
    return str(target_url)


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


@router.post("/branches/{title}/create")
async def create_branch(
    request: Request,
    title: str,
    branch_name: str = Form(...),
    source_branch: str = Form("main"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Create a new branch for a page."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        feature_flags = getattr(request.state, "feature_flags", None)
        if feature_flags is None:
            feature_flags = await SettingsService.get_feature_flags()
            request.state.feature_flags = feature_flags
        if not feature_flags.page_editing_enabled and not user.get("is_admin", False):
            logger.info(
                f"Branch creation blocked for user '{user.get('username')}' because page editing is disabled"
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Page editing is currently disabled by an administrator."
                },
            )

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - creating branch: {branch_name} for page: {title}"
            )
            return {"error": "Database not available"}

        # Validate inputs
        if not is_valid_title(title):
            return {"error": "Invalid page title"}

        if not is_valid_branch_name(branch_name):
            return {"error": "Invalid branch name"}

        # Create the branch
        success = await BranchService.create_branch(title, branch_name, source_branch)

        if success:
            redirect_url = _build_page_redirect_url(request, title, branch_name)
            return RedirectResponse(url=redirect_url, status_code=303)
        else:
            return {"error": "Failed to create branch"}
    except Exception as e:
        logger.error(f"Error creating branch {branch_name} for page {title}: {str(e)}")
        return {"error": "Failed to create branch"}


@router.post("/set-branch")
async def set_branch(
    request: Request, branch: str = Form(...), csrf_protect: CsrfProtect = Depends()
):
    """Set the global branch for the session."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)

        safe_branch = branch if is_safe_branch_parameter(branch) else "main"
        referer_header = request.headers.get("referer")
        # Simple validation - only allow relative URLs
        safe_referer = referer_header if referer_header and not urlparse(referer_header).scheme else "/"

        parsed = urlparse(safe_referer)
        if parsed.scheme or parsed.netloc:
            # External or malformed URL - redirect to home
            return RedirectResponse(url="/", status_code=303)

        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_params["branch"] = safe_branch
        new_query = urlencode(query_params, doseq=True)
        redirect_target = parsed.path or "/"
        if new_query:
            redirect_target = f"{redirect_target}?{new_query}"

        logger.info(f"Branch set to: {safe_branch}")
        return RedirectResponse(url=redirect_target, status_code=303)
    except Exception as e:
        logger.error(f"Error setting branch to {branch}: {str(e)}")
        # Simple validation - only allow relative URLs
        safe_referer = request.headers.get("referer", "/")
        if urlparse(safe_referer).scheme or urlparse(safe_referer).netloc:
            safe_referer = "/"
        return RedirectResponse(url=safe_referer, status_code=303)
