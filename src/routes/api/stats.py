"""
Stats API routes for WikiWare.
Provides API endpoints for statistics.
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from ...database import db_instance, get_users_collection

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

        # Get stats for the specific user
        users_collection = get_users_collection()
        user_stats = await users_collection.find_one(
            {"username": username},
            projection={"_id": 0, "total_edits": 1, "page_edits": 1},
        )

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
