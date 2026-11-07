"""
History routes for WikiWare.
Handles page version history and restoration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from difflib import HtmlDiff
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...middleware.rate_limiter import rate_limit
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...services.page_errors import PageServiceError, PageUnavailableError
from ...services.render_service import render_markdown, render_sources_html
from ...services.page_permissions import get_page_edit_denial
from ...services.settings_service import SettingsService
from ...utils.error_utils import render_error_page
from ...utils.template_env import get_templates
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()
templates = get_templates()

HISTORY_RATE_LIMIT = rate_limit(
    "history-view",
    detail="Too many history requests. Please wait 1 minute and try again.",
)


@dataclass
class HistoryViewDependencies:
    user: Any
    csrf_token: str
    signed_token: str
    db_online: bool
    branches: List[Any] = field(default_factory=list)


async def _prepare_history_view_dependencies(
    request: Request,
    csrf_protect: CsrfProtect,
    *,
    include_branches: bool = False,
) -> HistoryViewDependencies:
    """Gather shared request context for history views."""
    user = await AuthMiddleware.get_current_user(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    db_online = db_instance.is_connected

    branches: List[Any] = []
    if include_branches and db_online:
        try:
            branches = await BranchService.get_available_branches()
        except Exception as branch_error:
            logger.error(
                f"Error fetching branches for history view: {str(branch_error)}"
            )
            branches = []

    return HistoryViewDependencies(
        user=user,
        csrf_token=csrf_token,
        signed_token=signed_token,
        db_online=db_online,
        branches=branches,
    )


def _render_template_with_csrf(
    template_name: str,
    context: Dict[str, Any],
    csrf_protect: CsrfProtect,
    signed_token: str,
):
    """Render a template and ensure the CSRF cookie is set when available."""
    template = templates.TemplateResponse(template_name, context)
    if signed_token:
        csrf_protect.set_csrf_cookie(signed_token, template)
    return template


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
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch current page and historical versions ordered newest first."""

    try:
        current = await PageService.get_page(title, branch)
        history = await PageService.get_history(title, branch, limit=limit)
        if current is not None:
            current["revision_number"] = (history[0]["revision_number"] + 1) if history else 1
        versions = [current] if current else []
        return versions + (history if limit is None else history[: max(0, limit - len(versions))])
    except PageServiceError:
        raise
    except Exception as db_error:
        logger.error(
            f"Database error while fetching version list for {title} on branch {branch}: {str(db_error)}"
        )
        raise


async def _get_version_by_index(
    title: str,
    branch: str,
    version_index: int,
) -> Optional[Dict[str, Any]]:
    """Fetch a single version document by index (0=current, 1+ history)."""

    if version_index < 0:
        return None

    try:
        if version_index == 0:
            return await PageService.get_page(title, branch)
        history = await PageService.get_history(title, branch, limit=None)
        return history[version_index - 1] if version_index <= len(history) else None
    except PageServiceError:
        raise
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
            updated_at_display = (
                str(updated_at) if updated_at is not None else "Unknown"
            )

        summary_raw = doc.get("edit_summary")
        edit_summary = str(summary_raw).strip() if summary_raw is not None else ""

        entries.append(
            {
                "index": idx,
                "display_number": doc.get("revision_number", max(1, total_versions - idx)),
                "revision_id": doc.get("id") if idx > 0 else None,
                "author": doc.get("author", "Anonymous"),
                "updated_at_display": updated_at_display,
                "edit_summary": edit_summary,
                "document": doc,
                "is_current": idx == 0,
            }
        )

    return entries


@router.get(
    "/history/{title}",
    response_class=HTMLResponse,
    dependencies=[Depends(HISTORY_RATE_LIMIT)],
)
async def page_history(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View page history."""
    deps: Optional[HistoryViewDependencies] = None
    try:
        deps = await _prepare_history_view_dependencies(
            request, csrf_protect, include_branches=True
        )

        if not is_valid_title(title):
            logger.warning(f"Invalid title for history: {title} on branch: {branch}")
            context = {
                "request": request,
                "title": title,
                "versions": [],
                "error": "Invalid page title",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "history.html", context, csrf_protect, deps.signed_token
            )

        if not deps.db_online:
            raise PageUnavailableError()

        try:
            versions_raw = await _fetch_versions_for_history(
                title,
                branch,
                limit=None,
            )
            versions = _build_version_entries(versions_raw)
        except PageServiceError:
            raise
        except Exception as db_error:
            logger.error(
                f"Database error while fetching history for {title} on branch {branch}: {str(db_error)}"
            )
            context = {
                "request": request,
                "title": title,
                "versions": [],
                "error": "Database error occurred",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "history.html", context, csrf_protect, deps.signed_token
            )

        logger.info(f"History viewed: {title} on branch: {branch}")
        compare_defaults: Optional[Dict[str, Optional[int]]] = None
        if len(versions) > 1:
            compare_defaults = {
                "from_index": versions[1]["index"],
                "to_index": versions[0]["index"],
            }

        context = {
            "request": request,
            "title": title,
            "versions": versions,
            "branch": branch,
            "offline": not deps.db_online,
            "branches": deps.branches,
            "user": deps.user,
            "csrf_token": deps.csrf_token,
            "compare_defaults": compare_defaults,
        }
        return _render_template_with_csrf(
            "history.html", context, csrf_protect, deps.signed_token
        )
    except PageServiceError:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("History view failed: {}", exc)
        return render_error_page(request, title="History Error", message="History could not be displayed.", status_code=500)


@router.get(
    "/history/{title}/compare",
    response_class=HTMLResponse,
    dependencies=[Depends(HISTORY_RATE_LIMIT)],
)
async def compare_versions(
    request: Request,
    response: Response,
    title: str,
    branch: str = "main",
    from_version: int = 1,
    to_version: int = 0,
    from_revision: int | None = None,
    to_revision: int | None = None,
    csrf_protect: CsrfProtect = Depends(),
):
    """Compare two versions of a page and render a side-by-side diff."""

    deps: Optional[HistoryViewDependencies] = None
    try:
        deps = await _prepare_history_view_dependencies(
            request, csrf_protect, include_branches=True
        )

        if not is_valid_title(title):
            logger.warning(f"Invalid title for comparison: {title} on branch: {branch}")
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        if not deps.db_online:
            raise PageUnavailableError()

        try:
            versions_raw = await _fetch_versions_for_history(
                title,
                branch,
                limit=None,
            )
            version_entries = _build_version_entries(versions_raw)
        except PageServiceError:
            raise
        except Exception as db_error:
            logger.error(
                f"Database error while preparing comparison for {title} on branch {branch}: {str(db_error)}"
            )
            context = {
                "request": request,
                "title": title,
                "branch": branch,
                "versions": [],
                "compare_error": "Database error occurred",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "compare.html", context, csrf_protect, deps.signed_token
            )

        compare_error: Optional[str] = None
        diff_html: Optional[str] = None
        from_meta: Optional[Dict[str, Any]] = None
        to_meta: Optional[Dict[str, Any]] = None

        version_lookup = {entry["index"]: entry for entry in version_entries}
        if from_revision is not None:
            from_version = next((entry["index"] for entry in version_entries if (entry["revision_id"] or 0) == from_revision), -1)
        if to_revision is not None:
            to_version = next((entry["index"] for entry in version_entries if (entry["revision_id"] or 0) == to_revision), -1)

        if len(version_entries) < 2:
            compare_error = "Not enough versions to compare."
        elif from_version == to_version:
            compare_error = "Select two different versions to compare."
        elif from_version not in version_lookup or to_version not in version_lookup:
            compare_error = "Selected versions could not be found."
        else:
            try:
                from_page = version_lookup[from_version]["document"]
                to_page = version_lookup[to_version]["document"]
            except PageServiceError:
                raise
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

        context = {
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
            "branches": deps.branches,
            "user": deps.user,
            "csrf_token": deps.csrf_token,
        }
        return _render_template_with_csrf(
            "compare.html", context, csrf_protect, deps.signed_token
        )
    except PageServiceError:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("History view failed: {}", exc)
        return render_error_page(request, title="History Error", message="History could not be displayed.", status_code=500)


@router.get(
    "/history/{title}/{version_index}",
    response_class=HTMLResponse,
    dependencies=[Depends(HISTORY_RATE_LIMIT)],
)
async def view_version(
    request: Request,
    response: Response,
    title: str,
    version_index: int,
    branch: str = "main",
    revision_id: int | None = None,
    csrf_protect: CsrfProtect = Depends(),
):
    """View a specific version of a page."""
    deps: Optional[HistoryViewDependencies] = None
    try:
        deps = await _prepare_history_view_dependencies(
            request, csrf_protect, include_branches=True
        )

        if not is_valid_title(title):
            logger.warning(
                f"Invalid title for version view: {title} on branch: {branch}"
            )
            context = {
                "request": request,
                "title": title,
                "content": "",
                "error": "Invalid page title",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "edit.html", context, csrf_protect, deps.signed_token
            )

        if version_index < 0:
            logger.warning(
                f"Invalid version index: {version_index} for title: {title} on branch: {branch}"
            )
            context = {
                "request": request,
                "title": title,
                "content": "",
                "error": "Invalid version index",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "edit.html", context, csrf_protect, deps.signed_token
            )

        if not deps.db_online:
            raise PageUnavailableError()

        try:
            page = (
                await PageService.get_revision(title, branch, revision_id)
                if revision_id is not None
                else await _get_version_by_index(title, branch, version_index)
            )
        except PageServiceError:
            raise
        except Exception as db_error:
            logger.error(
                f"Database error while fetching version {version_index} for {title} on branch {branch}: {str(db_error)}"
            )
            context = {
                "request": request,
                "title": title,
                "content": "",
                "error": "Database error occurred",
                "user": deps.user,
                "csrf_token": deps.csrf_token,
            }
            return _render_template_with_csrf(
                "edit.html", context, csrf_protect, deps.signed_token
            )

        if not page:
            raise HTTPException(status_code=404, detail="Revision not found")

        try:
            rendered, _, sources = await render_markdown(page["content"])
            page["html_content"] = rendered + render_sources_html(sources)
        except Exception as md_error:
            logger.error(
                f"Error rendering markdown for version {version_index} of {title} on branch {branch}: {str(md_error)}"
            )
            raise

        try:
            total_history = len(await PageService.get_history(title, branch, limit=None))
            total_versions = 1 + total_history
            display_version_num = page.get("revision_number", max(1, total_versions - int(version_index)))
        except Exception:
            display_version_num = int(version_index)

        logger.info(f"Version viewed: {title} v{version_index} on branch: {branch}")
        context = {
            "request": request,
            "page": page,
            "version_num": display_version_num,
            "version_index": version_index,
            "revision_id": page.get("id") if version_index > 0 else None,
            "branch": branch,
            "offline": not deps.db_online,
            "branches": deps.branches,
            "user": deps.user,
            "csrf_token": deps.csrf_token,
        }
        return _render_template_with_csrf(
            "version.html", context, csrf_protect, deps.signed_token
        )
    except PageServiceError:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("History view failed: {}", exc)
        return render_error_page(request, title="History Error", message="History could not be displayed.", status_code=500)


@router.post("/restore/{title}/{version_index}")
async def restore_version(
    request: Request,
    title: str,
    version_index: int,
    branch: str = Form("main"),
    revision_id: int = Form(...),
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

        if version_index == 0:
            logger.info(
                f"Attempt to restore current version (no action): {title} v{version_index} on branch: {branch}"
            )
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)

        current_page = await PageService.get_page(title, branch)
        if current_page is None:
            raise HTTPException(status_code=404, detail="Page not found")
        feature_flags = getattr(request.state, "feature_flags", None)
        if feature_flags is None:
            feature_flags = await SettingsService.get_feature_flags()
        denial = await get_page_edit_denial(
            user, title, current_page,
            editing_enabled=feature_flags.page_editing_enabled,
        )
        if denial:
            return render_error_page(
                request, user=user, title="Access Denied", message=denial,
                status_code=403, branch=branch,
            )

        try:
            page = await PageService.get_revision(title, branch, revision_id)
        except PageServiceError:
            raise
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
            restored = await PageService.restore_version(
                title, branch, version_index, user["username"],
                revision_id=revision_id, actor=user,
            )
            if not restored:
                raise HTTPException(status_code=404, detail="Revision not found")
        except (PageServiceError, HTTPException):
            raise
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
    except CsrfProtectError as exc:
        return render_error_page(
            request, title="Invalid CSRF Token", message=exc.message,
            status_code=exc.status_code, branch=branch,
        )
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    except PageServiceError as exc:
        return render_error_page(
            request, title="Page operation failed", message=str(exc),
            status_code=exc.status_code, branch=branch,
        )
    except Exception as e:
        logger.error(
            f"Error restoring version {title} v{version_index} on branch {branch}: {str(e)}"
        )
        redirect_url = _build_page_redirect_url(
            request, title, branch, error="restore_error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)
