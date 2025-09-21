"""
Authentication middleware for WikiWare.
Handles session validation and user context.
"""

from typing import Optional, Dict, Any
from fastapi import Request, HTTPException
from loguru import logger
from ..services.user_service import UserService
from ..config import SESSION_COOKIE_NAME
from ..database import db_instance


class AuthMiddleware:
    """Middleware for handling authentication."""

    @staticmethod
    async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
        """
        Get current user from session cookie.

        Args:
            request: FastAPI request object

        Returns:
            User data if authenticated, None otherwise
        """
        try:
            # Get session cookie
            session_id = (
                request.cookies.get(SESSION_COOKIE_NAME)
                or request.cookies.get("__Host-user_session")
                or request.cookies.get("user_session")
            )
            if not session_id:
                return None

            # In offline mode, we can't validate against database
            if not db_instance.is_connected:
                return None

            # Get user from session
            user = await UserService.get_user_by_session(session_id)
            if not user or not user.get("is_active", True):
                return None

            return {
                "username": user["username"],
                "is_admin": user.get("is_admin", False),
            }

        except Exception as e:
            logger.warning(f"Error validating session: {str(e)}")
            return None

    @staticmethod
    async def require_auth(request: Request) -> Dict[str, Any]:
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
