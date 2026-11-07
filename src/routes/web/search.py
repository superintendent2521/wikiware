"""
Search routes for WikiWare.
Handles search functionality.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.analytics_service import AnalyticsService
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...services.page_errors import PageServiceError, PageUnavailableError
from ...utils.error_utils import render_error_page
from ...utils.validation import is_safe_branch_parameter
from ...utils.template_env import get_templates

router = APIRouter()

templates = get_templates()


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    response: Response,
    q: str = "",
    branch: str = "main",
    show_all: bool = False,
    page: int = Query(1, ge=1, le=1000000),
    csrf_protect: CsrfProtect = Depends(),
):
    """Search pages by query."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        if not db_instance.is_connected:
            raise PageUnavailableError()
        if not is_safe_branch_parameter(branch):
            raise HTTPException(status_code=400, detail="Invalid branch")

        # Get available branches
        branches = await BranchService.get_available_branches()

        pages = []
        page_size = 50
        offset = (page - 1) * page_size
        if q:
            pages = await PageService.search_pages(q, branch, limit=page_size + 1, offset=offset)
            if page == 1:
                await AnalyticsService.record_search(q, branch, min(page_size, len(pages)))
        elif show_all:
            pages = await PageService.get_pages_by_branch(branch, limit=page_size + 1, offset=offset)
            logger.info(
                f"Search accessed via show_all flag on branch '{branch}' returned {len(pages)} pages"
            )
        else:
            logger.info("Search accessed without query")

        template = templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "pages": pages[:page_size],
                "page_number": page,
                "has_next": len(pages) > page_size,
                "query": q,
                "branch": branch,
                "show_all": show_all,
                "offline": not db_instance.is_connected,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except PageServiceError:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Search failed: {}", exc)
        return render_error_page(request, title="Search Error", message="Search could not be completed.", status_code=500)
