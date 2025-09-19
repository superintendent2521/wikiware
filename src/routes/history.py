"""
History routes for WikiWare.
Handles page version history and restoration.
"""

from fastapi import APIRouter, Request, Form, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_csrf_protect import CsrfProtect
import markdown
from difflib import HtmlDiff
from typing import Any, Dict, List, Optional
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


def _build_page_redirect_url(
    request: Request, title: str, branch: str, **extra_params: str
) -> str:
    """Construct a safe internal URL to the page view with optional query parameters."""
    safe_branch = (
        branch if branch == "main" or is_safe_branch_parameter(branch) else "main"
    )
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


async def _fetch_versions_for_history(
    title: str,
    branch: str,
    *,
    limit: int = 100,
    pages_collection=None,
    history_collection=None,
) -> List[Dict[str, Any]]:
    """Fetch current page and historical versions ordered newest first."""

    if pages_collection is None:
        pages_collection = get_pages_collection()
    if history_collection is None:
        history_collection = get_history_collection()

    versions: List[Dict[str, Any]] = []

    try:
        if pages_collection is not None:
            current = await pages_collection.find_one({"title": title, "branch": branch})
            if current:
                versions.append(current)

        remaining = max(0, limit - len(versions))

        if remaining > 0 and history_collection is not None:
            history_cursor = (
                history_collection.find({"title": title, "branch": branch})
                .sort("updated_at", -1)
                .limit(remaining)
            )
            history_versions = await history_cursor.to_list(remaining)
            versions.extend(history_versions)

        return versions
    except Exception as db_error:
        logger.error(
            f"Database error while fetching version list for {title} on branch {branch}: {str(db_error)}"
        )
        raise


async def _get_version_by_index(
    title: str,
    branch: str,
    version_index: int,
    *,
    pages_collection=None,
    history_collection=None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single version document by index (0=current, 1+ history)."""

    if version_index < 0:
        return None

    if pages_collection is None:
        pages_collection = get_pages_collection()
    if history_collection is None:
        history_collection = get_history_collection()

    try:
        if version_index == 0:
            if pages_collection is None:
                return None
            return await pages_collection.find_one({"title": title, "branch": branch})

        if history_collection is None:
            return None

        cursor = (
            history_collection.find({"title": title, "branch": branch})
            .sort("updated_at", -1)
            .skip(version_index - 1)
            .limit(1)
        )
        results = await cursor.to_list(1)
        return results[0] if results else None
    except Exception as db_error:
        logger.error(
            f"Database error while fetching version {version_index} for {title} on branch {branch}: {str(db_error)}"
        )
        raise


def _build_version_entries(versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach metadata (display number, author, timestamps) to versions."""

    total_versions = len(versions)
    entries: List[Dict[str, Any]] = []

    for idx, doc in enumerate(versions):
        updated_at = doc.get("updated_at")
        if isinstance(updated_at, datetime):
            updated_at_display = updated_at.strftime("%Y-%m-%d %H:%M:%S")
        else:
            updated_at_display = str(updated_at) if updated_at is not None else "Unknown"

        entries.append(
            {
                "index": idx,
                "display_number": max(1, total_versions - idx),
                "author": doc.get("author", "Anonymous"),
                "updated_at_display": updated_at_display,
                "document": doc,
                "is_current": idx == 0,
            }
        )

    return entries


@router.get("/history/{title}", response_class=HTMLResponse)
async def page_history(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View page history."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        # Sanitize title
        if not is_valid_title(title):
            logger.warning(f"Invalid title for history: {title} on branch: {branch}")
            template = templates.TemplateResponse(
                "history.html",
                {
                    "request": request,
                    "title": title,
                    "versions": [],
                    "error": "Invalid page title",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not db_instance.is_connected:
            logger.warning(
                f"Database not connected - viewing history: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "history.html",
                {
                    "request": request,
                    "title": title,
                    "versions": [],
                    "offline": True,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        # Get available branches
        branches = await BranchService.get_available_branches()

        versions = []

        try:
            versions_raw = await _fetch_versions_for_history(
                title,
                branch,
                limit=100,
                pages_collection=pages_collection,
                history_collection=history_collection,
            )
            versions = _build_version_entries(versions_raw)
        except Exception as db_error:
            logger.error(
                f"Database error while fetching history for {title} on branch {branch}: {str(db_error)}"
            )
            template = templates.TemplateResponse(
                "history.html",
                {
                    "request": request,
                "title": title,
                "versions": [],
                "error": "Database error occurred",
                "user": user,
                "csrf_token": csrf_token,
            },
        )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        logger.info(f"History viewed: {title} on branch: {branch}")
        compare_defaults: Optional[Dict[str, Optional[int]]] = None
        if len(versions) > 1:
            compare_defaults = {
                "from_index": versions[1]["index"],
                "to_index": versions[0]["index"],
            }

        template = templates.TemplateResponse(
            "history.html",
            {
                "request": request,
                "title": title,
                "versions": versions,
                "branch": branch,
                "offline": not db_instance.is_connected,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
                "compare_defaults": compare_defaults,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(f"Error viewing history {title} on branch {branch}: {str(e)}")
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse(
            "history.html",
            {
                "request": request,
                "title": title,
                "versions": [],
                "error": "An error occurred while loading history",
                "user": user,
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.get("/history/{title}/compare", response_class=HTMLResponse)
async def compare_versions(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    from_version: int = 1,
    to_version: int = 0,
    csrf_protect: CsrfProtect = Depends(),
):
    """Compare two versions of a page and render a side-by-side diff."""

    try:
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        if not is_valid_title(title):
            logger.warning(f"Invalid title for comparison: {title} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        if not db_instance.is_connected:
            logger.warning(
                f"Database not connected - comparing versions: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "compare.html",
                {
                    "request": request,
                    "title": title,
                    "branch": branch,
                    "versions": [],
                    "compare_error": "Database not available",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        if pages_collection is None and history_collection is None:
            logger.error(
                f"Database collections not available - comparing versions: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "compare.html",
                {
                    "request": request,
                    "title": title,
                    "branch": branch,
                    "versions": [],
                    "compare_error": "Database not available",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        branches = await BranchService.get_available_branches()

        try:
            versions_raw = await _fetch_versions_for_history(
                title,
                branch,
                limit=100,
                pages_collection=pages_collection,
                history_collection=history_collection,
            )
            version_entries = _build_version_entries(versions_raw)
        except Exception as db_error:
            logger.error(
                f"Database error while preparing comparison for {title} on branch {branch}: {str(db_error)}"
            )
            template = templates.TemplateResponse(
                "compare.html",
                {
                    "request": request,
                    "title": title,
                    "branch": branch,
                    "versions": [],
                    "compare_error": "Database error occurred",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        compare_error: Optional[str] = None
        diff_html: Optional[str] = None
        from_meta: Optional[Dict[str, Any]] = None
        to_meta: Optional[Dict[str, Any]] = None

        version_lookup = {entry["index"]: entry for entry in version_entries}

        if len(version_entries) < 2:
            compare_error = "Not enough versions to compare."
        elif from_version == to_version:
            compare_error = "Select two different versions to compare."
        elif from_version not in version_lookup or to_version not in version_lookup:
            compare_error = "Selected versions could not be found."
        else:
            try:
                from_page = await _get_version_by_index(
                    title,
                    branch,
                    from_version,
                    pages_collection=pages_collection,
                    history_collection=history_collection,
                )
                to_page = await _get_version_by_index(
                    title,
                    branch,
                    to_version,
                    pages_collection=pages_collection,
                    history_collection=history_collection,
                )
            except Exception as db_error:
                logger.error(
                    f"Database error while fetching comparison versions for {title} on branch {branch}: {str(db_error)}"
                )
                compare_error = "Database error occurred while fetching versions."
                from_page = None
                to_page = None

            if from_page and to_page:
                from_meta = version_lookup[from_version]
                to_meta = version_lookup[to_version]

                diff_builder = HtmlDiff(wrapcolumn=80)
                diff_html = diff_builder.make_table(
                    from_page.get("content", "").splitlines(),
                    to_page.get("content", "").splitlines(),
                    f"Version {from_meta['display_number']}",
                    f"Version {to_meta['display_number']}",
                    context=True,
                    numlines=3,
                )
            elif compare_error is None:
                compare_error = "One or both selected versions could not be found."

        template = templates.TemplateResponse(
            "compare.html",
            {
                "request": request,
                "title": title,
                "branch": branch,
                "versions": version_entries,
                "from_version": from_version,
                "to_version": to_version,
                "from_meta": from_meta,
                "to_meta": to_meta,
                "diff_html": diff_html,
                "compare_error": compare_error,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(
            f"Error comparing versions for {title} on branch {branch}: {str(e)}"
        )
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse(
            "compare.html",
            {
                "request": request,
                "title": title,
                "branch": branch,
                "versions": [],
                "compare_error": "An error occurred while preparing the comparison.",
                "user": locals().get("user"),
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.get("/history/{title}/{version_index}", response_class=HTMLResponse)
async def view_version(
    request: Request,
    response: Response,
    title: str,
    version_index: int,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View a specific version of a page."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        # Sanitize title
        if not is_valid_title(title):
            logger.warning(
                f"Invalid title for version view: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "error": "Invalid page title",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Validate version index
        if version_index < 0:
            logger.warning(
                f"Invalid version index: {version_index} for title: {title} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "error": "Invalid version index",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not db_instance.is_connected:
            logger.warning(
                f"Database not connected - viewing version: {title} v{version_index} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "page.html",
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

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        # Get available branches
        branches = await BranchService.get_available_branches()

        if pages_collection is None or history_collection is None:
            logger.error(
                f"Database collections not available - viewing version: {title} v{version_index} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "error": "Database not available",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        page = None
        try:
            page = await _get_version_by_index(
                title,
                branch,
                version_index,
                pages_collection=pages_collection,
                history_collection=history_collection,
            )
        except Exception as db_error:
            logger.error(
                f"Database error while fetching version {version_index} for {title} on branch {branch}: {str(db_error)}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "error": "Database error occurred",
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        if not page:
            logger.info(
                f"Version not found - viewing edit page: {title} v{version_index} on branch: {branch}"
            )
            template = templates.TemplateResponse(
                "edit.html",
                {
                    "request": request,
                    "title": title,
                    "content": "",
                    "offline": False,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        try:
            # First process internal links with our custom processor
            processed_content = await process_internal_links(page["content"])
            # Then render as Markdown (with any remaining Markdown syntax)
            md = markdown.Markdown()
            page["html_content"] = sanitize_html(md.convert(processed_content))
        except Exception as md_error:
            logger.error(
                f"Error rendering markdown for version {version_index} of {title} on branch {branch}: {str(md_error)}"
            )
            page["html_content"] = page["content"]  # Fallback to raw content

        # Compute display version number so that newer versions have higher numbers
        try:
            total_history = 0
            if history_collection is not None:
                total_history = await history_collection.count_documents(
                    {"title": title, "branch": branch}
                )
            total_versions = 1 + total_history  # include current
            display_version_num = max(1, total_versions - int(version_index))
        except Exception:
            # Fallback to original index if counting fails
            display_version_num = int(version_index)

        logger.info(f"Version viewed: {title} v{version_index} on branch: {branch}")
        template = templates.TemplateResponse(
            "version.html",
            {
                "request": request,
                "page": page,
                "version_num": display_version_num,
                "version_index": version_index,
                "branch": branch,
                "offline": not db_instance.is_connected,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except Exception as e:
        logger.error(
            f"Error viewing version {title} v{version_index} on branch {branch}: {str(e)}"
        )
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
                "error": "An error occurred while loading version",
                "user": user,
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


@router.post("/restore/{title}/{version_index}")
async def restore_version(
    request: Request,
    title: str,
    version_index: int,
    branch: str = Form("main"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Restore a page to a previous version."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if not is_safe_branch_parameter(branch):
            logger.warning(
                f"Invalid branch '{branch}' while restoring version for {title}, defaulting to main"
            )
            branch = "main"

        # Sanitize title
        if not is_valid_title(title):
            logger.warning(f"Invalid title for restore: {title} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        # Validate version index
        if version_index < 0:
            logger.warning(
                f"Invalid version index: {version_index} for title: {title} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - restoring version: {title} v{version_index} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(
                request, title, branch, error="database_not_available"
            )
            return RedirectResponse(url=redirect_url, status_code=303)

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        if pages_collection is None or history_collection is None:
            logger.error(
                f"Database collections not available - restoring version: {title} v{version_index} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(
                request, title, branch, error="database_not_available"
            )
            return RedirectResponse(url=redirect_url, status_code=303)

        if version_index == 0:
            logger.info(
                f"Attempt to restore current version (no action): {title} v{version_index} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        try:
            page = await _get_version_by_index(
                title,
                branch,
                version_index,
                pages_collection=pages_collection,
                history_collection=history_collection,
            )
        except Exception as db_error:
            logger.error(
                f"Database error while fetching version {version_index} for restore {title} on branch {branch}: {str(db_error)}"
            )
            redirect_url = _build_page_redirect_url(
                request, title, branch, error="database_error"
            )
            return RedirectResponse(url=redirect_url, status_code=303)

        if not page:
            logger.error(
                f"Version not found for restore: {title} v{version_index} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(
                request, title, branch, error="version_not_found"
            )
            return RedirectResponse(url=redirect_url, status_code=303)

        try:
            # Save current version to history before restoring
            current_page = await pages_collection.find_one(
                {"title": title, "branch": branch}
            )
            if current_page:
                history_item = {
                    "title": title,
                    "content": current_page["content"],
                    "author": current_page.get("author", "Anonymous"),
                    "branch": branch,
                    "updated_at": current_page["updated_at"],
                }
                await history_collection.insert_one(history_item)

            # Restore the version
            await pages_collection.update_one(
                {"title": title, "branch": branch},
                {
                    "$set": {
                        "content": page["content"],
                        "author": page.get("author", "Anonymous"),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
        except Exception as db_error:
            logger.error(
                f"Database error while restoring version {version_index} of {title} on branch {branch}: {str(db_error)}"
            )
            redirect_url = _build_page_redirect_url(
                request, title, branch, error="restore_failed"
            )
            return RedirectResponse(url=redirect_url, status_code=303)

        logger.info(f"Version restored: {title} v{version_index} on branch: {branch}")
        redirect_url = _build_page_redirect_url(request, title, branch, restored="true")
        return RedirectResponse(url=redirect_url, status_code=303)
    except Exception as e:
        logger.error(
            f"Error restoring version {title} v{version_index} on branch {branch}: {str(e)}"
        )
        redirect_url = _build_page_redirect_url(
            request, title, branch, error="restore_error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)
