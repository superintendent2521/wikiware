"""
Authentication middleware for WikiWare.
Handles session validation and user context.
"""

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from loguru import logger

from ..config import SESSION_COOKIE_NAME
from ..database import db_instance
from ..services.user_service import UserService

SESSION_COOKIE_CANDIDATES = (
    SESSION_COOKIE_NAME,
    "__Host-user_session",
    "user_session",
)

UserPayload = Dict[str, Any]


def _get_session_cookie(request: Request) -> Optional[str]:
    """Return the first matching session cookie value or None."""
    for cookie_name in SESSION_COOKIE_CANDIDATES:
        value = request.cookies.get(cookie_name)
        if value:
            return value
    return None


class AuthMiddleware:
    """Middleware for handling authentication."""

    @staticmethod
    async def get_current_user(request: Request) -> Optional[UserPayload]:
        """
        Get current user from session cookie.

        Args:
            request: FastAPI request object

        Returns:
            User data if authenticated, None otherwise
        """
        try:
            session_id = _get_session_cookie(request)
            if not session_id or not db_instance.is_connected:
                return None

            user = await UserService.get_user_by_session(session_id)
            if not user or not user.get("is_active", True):
                return None

            return {
                "username": user["username"],
                "is_admin": user.get("is_admin", False),
            }
        except Exception as exc:  # IGNORE W0718
            logger.warning("Error validating session: {}", exc)
            return None

    @staticmethod
    async def require_auth(request: Request) -> UserPayload:
        """
        Require authentication for a request.

        Args:
            request: FastAPI request object

        Returns:
            User data if authenticated

        Raises:
            HTTPException: If user is not authenticated
        """
        user = await AuthMiddleware.get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user

    @staticmethod
    async def is_admin(request: Request) -> bool:
        """
        Check if current user is an admin.

        Args:
            request: FastAPI request object

        Returns:
            True if user is admin, False otherwise
        """
        user = await AuthMiddleware.get_current_user(request)
        return user is not None and user.get("is_admin", False)
