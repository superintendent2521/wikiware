"""
Stats API routes for WikiWare.
Provides API endpoints for statistics.
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from ...database import db_instance
from ...services.user_service import UserService

router = APIRouter()


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

        user_stats = await UserService.get_user_by_username(username)

        if not user_stats:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "total_edits": user_stats.get("total_edits", 0),
            "page_edits": user_stats.get("page_edits", {}),
        }

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error getting user stats for {username}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
