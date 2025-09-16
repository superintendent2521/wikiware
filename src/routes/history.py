"""
History routes for WikiWare.
Handles page version history and restoration.
"""

from fastapi import APIRouter, Request, Form, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_csrf_protect import CsrfProtect
import markdown
from ..utils.link_processor import process_internal_links
from ..utils.sanitizer import sanitize_html
from ..database import get_pages_collection, get_history_collection, db_instance
from ..services.branch_service import BranchService
from ..utils.validation import is_valid_title, is_safe_branch_parameter
from ..config import TEMPLATE_DIR
from ..middleware.auth_middleware import AuthMiddleware
from datetime import datetime, timezone
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _build_page_redirect_url(request: Request, title: str, branch: str, **extra_params: str) -> str:
    """Construct a safe internal URL to the page view with optional query parameters."""
    safe_branch = branch if branch == "main" or is_safe_branch_parameter(branch) else "main"
    target_url = request.url_for("get_page", title=title)
    query_params = {}
    if safe_branch != "main":
        query_params["branch"] = safe_branch
    for key, value in extra_params.items():
        if value is not None:
            query_params[key] = value
    if query_params:
        target_url = target_url.include_query_params(**query_params)
    return str(target_url)


@router.get("/history/{title}", response_class=HTMLResponse)
async def page_history(request: Request, response: Response, title: str, branch: str = "main", csrf_protect: CsrfProtect = Depends()):
    """View page history."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        
        # Sanitize title
        if not is_valid_title(title):
            logger.warning(f"Invalid title for history: {title} on branch: {branch}")
            template = templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Invalid page title", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing history: {title} on branch: {branch}")
            template = templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "offline": True, "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        # Get available branches
        branches = await BranchService.get_available_branches()

        versions = []

        try:
            if history_collection is not None:
                # Get history versions for the specific branch
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                # Get current version
                if pages_collection is not None:
                    current = await pages_collection.find_one({"title": title, "branch": branch})
                    if current:
                        versions.insert(0, current)  # Add current version at the beginning
        except Exception as db_error:
            logger.error(f"Database error while fetching history for {title} on branch {branch}: {str(db_error)}")
            template = templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Database error occurred", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        logger.info(f"History viewed: {title} on branch: {branch}")
        template = templates.TemplateResponse("history.html", {
            "request": request,
            "title": title,
            "versions": versions,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "branches": branches,
            "user": user,
            "csrf_token": csrf_token
        })
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(f"Error viewing history {title} on branch {branch}: {str(e)}")
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "An error occurred while loading history", "csrf_token": csrf_token_e})
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.get("/history/{title}/{version_index}", response_class=HTMLResponse)
async def view_version(request: Request, response: Response, title: str, version_index: int, branch: str = "main", csrf_protect: CsrfProtect = Depends()):
    """View a specific version of a page."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        
        # Sanitize title
        if not is_valid_title(title):
            logger.warning(f"Invalid title for version view: {title} on branch: {branch}")
            template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid page title", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title} on branch: {branch}")
            template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid version index", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing version: {title} v{version_index} on branch: {branch}")
            template = templates.TemplateResponse("page.html", {"request": request, "title": title, "content": "", "offline": True, "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        # Get available branches
        branches = await BranchService.get_available_branches()

        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - viewing version: {title} v{version_index} on branch: {branch}")
            template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database not available", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        page = None
        try:
            if version_index == 0:
                # Current version
                page = await pages_collection.find_one({"title": title, "branch": branch})
            else:
                # Historical version
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for {title} on branch {branch}: {str(db_error)}")
            template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database error occurred", "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not page:
            logger.info(f"Version not found - viewing edit page: {title} v{version_index} on branch: {branch}")
            template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False, "user": user, "csrf_token": csrf_token})
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        try:
            # First process internal links with our custom processor
            processed_content = await process_internal_links(page["content"])
            # Then render as Markdown (with any remaining Markdown syntax)
            md = markdown.Markdown()
            page["html_content"] = sanitize_html(md.convert(processed_content))
        except Exception as md_error:
            logger.error(f"Error rendering markdown for version {version_index} of {title} on branch {branch}: {str(md_error)}")
            page["html_content"] = page["content"]  # Fallback to raw content

        # Compute display version number so that newer versions have higher numbers
        try:
            total_history = 0
            if history_collection is not None:
                total_history = await history_collection.count_documents({"title": title, "branch": branch})
            total_versions = 1 + total_history  # include current
            display_version_num = max(1, total_versions - int(version_index))
        except Exception:
            # Fallback to original index if counting fails
            display_version_num = int(version_index)

        logger.info(f"Version viewed: {title} v{version_index} on branch: {branch}")
        template = templates.TemplateResponse("version.html", {
            "request": request,
            "page": page,
            "version_num": display_version_num,
            "version_index": version_index,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "branches": branches,
            "user": user,
            "csrf_token": csrf_token
        })
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(f"Error viewing version {title} v{version_index} on branch {branch}: {str(e)}")
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "An error occurred while loading version", "csrf_token": csrf_token_e})
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.post("/restore/{title}/{version_index}")
async def restore_version(request: Request, title: str, version_index: int, branch: str = Form("main"), csrf_protect: CsrfProtect = Depends()):
    """Restore a page to a previous version."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if not is_safe_branch_parameter(branch):
            logger.warning(f"Invalid branch '{branch}' while restoring version for {title}, defaulting to main")
            branch = "main"

        # Sanitize title
        if not is_valid_title(title):
            logger.warning(f"Invalid title for restore: {title} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        if not db_instance.is_connected:
            logger.error(f"Database not connected - restoring version: {title} v{version_index} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch, error="database_not_available")
            return RedirectResponse(url=redirect_url, status_code=303)

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - restoring version: {title} v{version_index} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch, error="database_not_available")
            return RedirectResponse(url=redirect_url, status_code=303)

        page = None
        try:
            if version_index == 0:
                # Current version - nothing to restore
                logger.info(f"Attempt to restore current version (no action): {title} v{version_index} on branch: {branch}")
                redirect_url = _build_page_redirect_url(request, title, branch)
                return RedirectResponse(url=redirect_url, status_code=303)
            else:
                # Historical version
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for restore {title} on branch {branch}: {str(db_error)}")
            redirect_url = _build_page_redirect_url(request, title, branch, error="database_error")
            return RedirectResponse(url=redirect_url, status_code=303)

        if not page:
            logger.error(f"Version not found for restore: {title} v{version_index} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch, error="version_not_found")
            return RedirectResponse(url=redirect_url, status_code=303)

        try:
            # Save current version to history before restoring
            current_page = await pages_collection.find_one({"title": title, "branch": branch})
            if current_page:
                history_item = {
                    "title": title,
                    "content": current_page["content"],
                    "author": current_page.get("author", "Anonymous"),
                    "branch": branch,
                    "updated_at": current_page["updated_at"]
                }
                await history_collection.insert_one(history_item)

            # Restore the version
            await pages_collection.update_one(
                {"title": title, "branch": branch},
                {"$set": {
                    "content": page["content"],
                    "author": page.get("author", "Anonymous"),
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
        except Exception as db_error:
            logger.error(f"Database error while restoring version {version_index} of {title} on branch {branch}: {str(db_error)}")
            redirect_url = _build_page_redirect_url(request, title, branch, error="restore_failed")
            return RedirectResponse(url=redirect_url, status_code=303)

        logger.info(f"Version restored: {title} v{version_index} on branch: {branch}")
        redirect_url = _build_page_redirect_url(request, title, branch, restored="true")
        return RedirectResponse(url=redirect_url, status_code=303)
    except Exception as e:
        logger.error(f"Error restoring version {title} v{version_index} on branch {branch}: {str(e)}")
        redirect_url = _build_page_redirect_url(request, title, branch, error="restore_error")
        return RedirectResponse(url=redirect_url, status_code=303)
