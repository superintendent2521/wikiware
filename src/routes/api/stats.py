"""
Stats routes for WikiWare.
Handles statistics API endpoints.
"""

from fastapi import APIRouter, HTTPException

from ...database import db_instance
from ...stats import get_user_edit_stats

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
        raise HTTPException(status_code=500, detail="Internal server error")
