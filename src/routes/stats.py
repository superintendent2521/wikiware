"""
Stats routes for WikiWare.
Handles statistics display.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ..database import db_instance
from ..middleware.auth_middleware import AuthMiddleware
from ..services.branch_service import BranchService
from ..stats import get_stats, get_user_edit_stats
from ..utils.template_env import get_templates

router = APIRouter()

templates = get_templates()


# Context processor to inject global stats into all templates
async def global_stats_context(request: Request):
    """Inject global statistics into all templates."""
    if not db_instance.is_connected:
        return {
            "global": {
                "edits": 0,
                "pages": 0,
                "characters": 0,
                "images": 0,
                "last_updated": None,
            }
        }

    stats = await get_stats()
    return {
        "global": {
            "edits": stats["total_edits"],
            "pages": stats["total_pages"],
            "characters": stats["total_characters"],
            "images": stats["total_images"],
            "last_updated": stats["last_updated"],
        }
    }


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    response: Response,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Display wiki statistics page."""
    try:
        # Get current user
        user = await AuthMiddleware.get_current_user(request)
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

        if not db_instance.is_connected:
            logger.warning("Database not connected - viewing stats")
            template = templates.TemplateResponse(
                "stats.html",
                {
                    "request": request,
                    "offline": True,
                    "branch": branch,
                    "user": user,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template

        # Get available branches
        branches = await BranchService.get_available_branches()

        # Get statistics
        stats = await get_stats()
        template = templates.TemplateResponse(
            "stats.html",
            {
                "request": request,
                "total_edits": stats["total_edits"],
                "total_characters": stats["total_characters"],
                "total_pages": stats["total_pages"],
                "total_images": stats["total_images"],
                "last_updated": stats["last_updated"],
                "offline": False,
                "branch": branch,
                "branches": branches,
                "user": user,
                "csrf_token": csrf_token,
                "user_edit_stats": stats["user_edit_stats"],
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
    except HTTPException as exc:
        raise exc
    except Exception as e:
        logger.error(f"Error viewing stats page: {str(e)}")
        try:
            csrf_token_e, signed_token_e = csrf_protect.generate_csrf_tokens()
        except Exception:
            csrf_token_e, signed_token_e = "", ""
        template = templates.TemplateResponse(
            "stats.html",
            {
                "request": request,
                "offline": True,
                "branch": branch,
                "csrf_token": csrf_token_e,
            },
        )
        if signed_token_e:
            csrf_protect.set_csrf_cookie(signed_token_e, template)
        return template


# Register the context processor with Jinja2
templates.env.globals.update(global_stats_context=global_stats_context)


@router.get("/stats/{username}")
async def get_user_stats(username: str):
    """
    Get edit statistics for a specific user.

    Args:
        username: Username to get statistics for

    Returns:
        dict: User edit statistics or 404 if user not found
    """
    try:
        if not db_instance.is_connected:
            raise HTTPException(status_code=503, detail="Database not available")

        # Get all user stats
        all_user_stats = await get_user_edit_stats()

        # Check if user exists
        if username not in all_user_stats:
            raise HTTPException(status_code=404, detail="User not found")

        # Return only the requested user's stats
        return all_user_stats[username]

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error getting user stats for {username}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
