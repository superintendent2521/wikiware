"""
Logs API routes for WikiWare.
Provides paginated access to system actions (edits, branch creations).
"""

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi_csrf_protect import CsrfProtect
from typing import Dict, Any, Optional
from ..utils.logs import LogUtils
from ..middleware.auth_middleware import AuthMiddleware
from loguru import logger

router = APIRouter()

@router.get("/api/logs", response_model=Dict[str, Any])
async def get_logs(
    request: Request,
    page: int = 1, 
    limit: int = 50, 
    action_type: Optional[str] = None,
    csrf_protect: CsrfProtect = Depends()
):
    """
    Get paginated system logs with optional filtering by action type.
    
    Args:
        page: Page number (1-indexed)
        limit: Number of items per page (max 50)
        action_type: Filter by action type ("edit", "branch_create", or None for all)
    
    Returns:
        Dictionary containing:
        - items: List of log entries
        - total: Total number of items
        - page: Current page number
        - pages: Total number of pages
        - limit: Items per page
    """
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)
        
        # Use the utility function   for core logic
        result = await LogUtils.get_paginated_logs(page, limit, action_type)
        return result
        
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
