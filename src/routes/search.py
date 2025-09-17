"""
Search routes for WikiWare.
Handles search functionality.
"""

from fastapi import APIRouter, Request, Depends, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi_csrf_protect import CsrfProtect
from ..services.page_service import PageService
from ..services.branch_service import BranchService
from ..database import db_instance
from ..config import TEMPLATE_DIR
from ..middleware.auth_middleware import AuthMiddleware
from loguru import logger

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


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
