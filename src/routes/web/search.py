"""
Search routes for WikiWare.
Handles search functionality (web interface).
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.branch_service import BranchService
from ...services.page_service import PageService
from ...utils.template_env import get_templates

router = APIRouter()

templates = get_templates()


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    response: Response,
    q: str = "",
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Search pages by query."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        if not db_instance.is_connected:
            logger.warning("Database not connected - search attempted")
            template = templates.TemplateResponse(
                "search.html",
                {
                    "request": request,
                    "pages": [],
                    "query": q,
                    "offline": True,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Get available branches
        branches = await BranchService.get_available_branches()

        pages = []
        if q:
            pages = await PageService.search_pages(q, branch)
        else:
            logger.info("Search accessed without query")

        template = templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "pages": pages,
                "query": q,
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
        logger.error(f"Error during search '{q}' on branch '{branch}': {str(e)}")
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "pages": [],
                "query": q,
                "offline": True,
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template
