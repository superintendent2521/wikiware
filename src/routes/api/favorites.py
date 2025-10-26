"""
API routes for managing user favorites.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.analytics_service import AnalyticsService
from ...services.page_service import PageService
from ...services.user_service import UserService
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()


def _normalize_branch(branch: str) -> str:
    """Normalize and validate the branch parameter."""
    normalized = (branch or "main").strip() or "main"
    if normalized != "main" and not is_safe_branch_parameter(normalized):
        raise HTTPException(status_code=400, detail="Invalid branch parameter")
    return normalized


def _normalize_title(title: str) -> str:
    """Trim and validate a page title."""
    normalized = title.strip()
    if not normalized or not is_valid_title(normalized):
        raise HTTPException(status_code=400, detail="Invalid page title")
    return normalized


async def _ensure_database_connected() -> None:
    """Ensure the database connection is available."""
    if not db_instance.is_connected:
        raise HTTPException(status_code=503, detail="Database not available")


async def _favorites_response(username: str) -> Dict[str, Any]:
    """Build a standard favorites response for the given user."""
    favorites = await UserService.list_favorites(username)
    if favorites is None:
        raise HTTPException(status_code=500, detail="Failed to load favorites")
    return {"favorites": favorites}


@router.get("/favorites")
async def list_favorites(request: Request) -> Dict[str, Any]:
    """Return the authenticated user's favorites."""
    user = await AuthMiddleware.require_auth(request)
    await _ensure_database_connected()
    return await _favorites_response(user["username"])


@router.post("/favorites/{title}")
async def add_favorite(
    title: str,
    request: Request,
    branch: str = "main",
) -> Dict[str, Any]:
    """Add a page to the authenticated user's favorites."""
    user = await AuthMiddleware.require_auth(request)
    await _ensure_database_connected()

    normalized_title = _normalize_title(title)
    normalized_branch = _normalize_branch(branch)

    page = await PageService.get_page(normalized_title, normalized_branch)
    if page is None:
        logger.info(
            f"Favorite add skipped because page '{normalized_title}' "
            f"(branch: {normalized_branch}) was not found"
        )
        raise HTTPException(status_code=404, detail="Page not found")

    existing_favorites = await UserService.list_favorites(user["username"])
    if existing_favorites is None:
        raise HTTPException(status_code=500, detail="Failed to load favorites")

    if any(
        fav["title"] == normalized_title and fav["branch"] == normalized_branch
        for fav in existing_favorites
    ):
        logger.info(
            f"User '{user['username']}' attempted to re-favorite page "
            f"'{normalized_title}' on branch '{normalized_branch}'"
        )
        return {
            "favorites": existing_favorites,
            "status": "already_favorited",
            "message": "This page is already in your favorites.",
        }

    success = await UserService.add_favorite(
        user["username"], normalized_title, normalized_branch
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update favorites")

    await AnalyticsService.record_favorite_added(normalized_title, normalized_branch)

    logger.info(
        f"User '{user['username']}' favorited page '{normalized_title}' "
        f"on branch '{normalized_branch}'"
    )
    response = await _favorites_response(user["username"])
    response["status"] = "favorited"
    return response


@router.delete("/favorites/{title}")
async def remove_favorite(
    title: str,
    request: Request,
    branch: str = "main",
) -> Dict[str, Any]:
    """Remove a page from the authenticated user's favorites."""
    user = await AuthMiddleware.require_auth(request)
    await _ensure_database_connected()

    normalized_title = _normalize_title(title)
    normalized_branch = _normalize_branch(branch)

    success = await UserService.remove_favorite(
        user["username"],
        normalized_title,
        normalized_branch,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update favorites")

    await AnalyticsService.record_favorite_removed(normalized_title, normalized_branch)
    logger.info(
        f"User '{user['username']}' removed favorite '{normalized_title}' "
        f"on branch '{normalized_branch}'"
    )
    response = await _favorites_response(user["username"])
    response["status"] = "unfavorited"
    return response
