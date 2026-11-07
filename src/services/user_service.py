"""User, favorite, and session operations backed by PostgreSQL."""

import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from passlib.context import CryptContext

from ..database import db_instance
from ..models.user import UserRegistration
from ..utils.sql import execute, execute_returning, fetch_all, fetch_one

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def _user_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    result = dict(row)
    if "id" in result:
        result.setdefault("_id", result["id"])
    result.setdefault("favorites", [])
    result.setdefault("page_edits", {})
    return result


async def _page_edits(user_id: int) -> Dict[str, int]:
    rows = await fetch_all(
        "SELECT p.title, e.edit_count FROM user_page_edit_counts e "
        "JOIN pages p ON p.id = e.page_id WHERE e.user_id = :user_id "
        "ORDER BY p.title",
        {"user_id": user_id},
    )
    return {str(row["title"]): int(row["edit_count"]) for row in rows}


class UserService:
    """Service class for user-related operations."""

    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    async def list_users() -> List[Dict[str, Any]]:
        """Return the small user projection used by admin/editor screens."""
        if not db_instance.is_connected:
            return []
        rows = await fetch_all(
            "SELECT id, username, is_admin, is_active, created_at FROM users ORDER BY username"
        )
        return [_user_row(row) or {} for row in rows]

    @staticmethod
    async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
        if not db_instance.is_connected:
            return None
        try:
            user = await fetch_one(
                "SELECT id, username, password_hash, created_at, is_active, "
                "is_admin, total_edits, password_changed_at, "
                "last_collection_export_at FROM users WHERE username = :username",
                {"username": username},
            )
            result = _user_row(user)
            if result is not None:
                result["page_edits"] = await _page_edits(result["id"])
            return result
        except Exception as exc:
            logger.error("Error getting user {}: {}", username, exc)
            return None

    @staticmethod
    async def list_favorites(username: str) -> Optional[List[Dict[str, str]]]:
        if not db_instance.is_connected:
            return None
        try:
            user = await fetch_one(
                "SELECT id FROM users WHERE username = :username", {"username": username}
            )
            if user is None:
                return None
            rows = await fetch_all(
                "SELECT p.title, b.name AS branch FROM favorites f "
                "JOIN page_branches b ON b.id = f.page_branch_id "
                "JOIN pages p ON p.id = b.page_id "
                "WHERE f.user_id = :user_id ORDER BY f.created_at",
                {"user_id": user["id"]},
            )
            return [
                {"title": str(row["title"]), "branch": (row.get("branch") or "main")}
                for row in rows
                if row.get("title")
            ]
        except Exception as exc:
            logger.error("Error listing favorites for {}: {}", username, exc)
            return None

    @staticmethod
    async def add_favorite(username: str, title: str, branch: str = "main") -> bool:
        if not db_instance.is_connected:
            return False
        try:
            user = await fetch_one(
                "SELECT id FROM users WHERE username = :username", {"username": username}
            )
            if user is None:
                return False
            await execute(
                "INSERT INTO favorites (user_id, page_branch_id) "
                "SELECT :user_id, b.id FROM page_branches b JOIN pages p ON p.id = b.page_id "
                "WHERE p.title = :title AND b.name = :branch "
                "ON CONFLICT (user_id, page_branch_id) DO NOTHING",
                {"user_id": user["id"], "title": title, "branch": branch or "main"},
            )
            return True
        except Exception as exc:
            logger.error("Error adding favorite {} for {}: {}", title, username, exc)
            return False

    @staticmethod
    async def remove_favorite(username: str, title: str, branch: str = "main") -> bool:
        if not db_instance.is_connected:
            return False
        try:
            user = await fetch_one(
                "SELECT id FROM users WHERE username = :username", {"username": username}
            )
            if user is None:
                return False
            await execute(
                "DELETE FROM favorites f USING page_branches b, pages p "
                "WHERE f.page_branch_id = b.id AND b.page_id = p.id AND f.user_id = :user_id "
                "AND p.title = :title AND b.name = :branch",
                {"user_id": user["id"], "title": title, "branch": branch or "main"},
            )
            return True
        except Exception as exc:
            logger.error("Error removing favorite {} for {}: {}", title, username, exc)
            return False

    @staticmethod
    async def create_user(user_data: UserRegistration) -> Optional[Dict[str, Any]]:
        if not db_instance.is_connected:
            return None
        try:
            if await UserService.get_user_by_username(user_data.username):
                return None
            row = await execute_returning(
                "INSERT INTO users (username, password_hash, created_at, is_active, "
                "is_admin, total_edits) VALUES (:username, :password_hash, "
                ":created_at, TRUE, FALSE, 0) "
                "RETURNING id, username, password_hash, created_at, is_active, is_admin, "
                "total_edits",
                {
                    "username": user_data.username,
                    "password_hash": await asyncio.to_thread(UserService.hash_password, user_data.password),
                    "created_at": datetime.now(timezone.utc),
                },
            )
            return _user_row(row)
        except Exception as exc:
            logger.error("Error creating user {}: {}", user_data.username, exc)
            return None

    @staticmethod
    async def authenticate_user(
        username: str,
        password: str,
        client_ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> Optional[Dict[str, Any]]:
        try:
            user = await UserService.get_user_by_username(username)
            if not user or not user.get("is_active", True):
                return None
            verified = await asyncio.to_thread(UserService.verify_password, password, user["password_hash"])
            return user if verified else None
        except Exception as exc:
            logger.error("Error authenticating user {}: {}", username, exc)
            return None

    @staticmethod
    async def change_password(
        username: str, current_password: str, new_password: str
    ) -> Tuple[bool, str]:
        if not db_instance.is_connected:
            return False, "offline"
        try:
            user = await UserService.get_user_by_username(username)
            if not user:
                return False, "user_not_found"
            if not await asyncio.to_thread(UserService.verify_password, current_password, user["password_hash"]):
                return False, "invalid_current_password"
            count = await execute(
                "UPDATE users SET password_hash = :password_hash, "
                "password_changed_at = :changed_at WHERE id = :user_id",
                {
                    "password_hash": await asyncio.to_thread(UserService.hash_password, new_password),
                    "changed_at": datetime.now(timezone.utc),
                    "user_id": user["id"],
                },
            )
            return count > 0, "" if count > 0 else "update_failed"
        except Exception as exc:
            logger.error("Error changing password for {}: {}", username, exc)
            return False, "error"

    @staticmethod
    async def create_session(user_id: str) -> Optional[str]:
        if not db_instance.is_connected:
            return None
        try:
            session_id = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            count = await execute(
                "INSERT INTO sessions (session_id, user_id, created_at, expires_at) "
                "SELECT :session_id, id, :created_at, :expires_at FROM users "
                "WHERE username = :user_id",
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "created_at": now,
                    "expires_at": now + timedelta(hours=24),
                },
            )
            return session_id if count else None
        except Exception as exc:
            logger.error("Error creating session for {}: {}", user_id, exc)
            return None

    @staticmethod
    async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
        if not db_instance.is_connected:
            return None
        try:
            session = await fetch_one(
                "SELECT session_id, user_id, created_at, expires_at FROM sessions "
                "WHERE session_id = :session_id",
                {"session_id": session_id},
            )
            if not session:
                return None
            expires_at = session.get("expires_at")
            if isinstance(expires_at, datetime):
                expires_at = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
                if expires_at > datetime.now(timezone.utc):
                    return session
            await execute("DELETE FROM sessions WHERE session_id = :session_id", {"session_id": session_id})
            return None
        except Exception as exc:
            logger.error("Error getting session {}: {}", session_id, exc)
            return None

    @staticmethod
    async def delete_session(session_id: str) -> bool:
        if not db_instance.is_connected:
            return False
        try:
            return await execute(
                "DELETE FROM sessions WHERE session_id = :session_id", {"session_id": session_id}
            ) > 0
        except Exception as exc:
            logger.error("Error deleting session {}: {}", session_id, exc)
            return False

    @staticmethod
    async def get_user_by_session(session_id: str) -> Optional[Dict[str, Any]]:
        session = await UserService.get_session(session_id)
        if not session:
            return None
        try:
            user = await fetch_one(
                "SELECT id, username, password_hash, created_at, is_active, is_admin, "
                "total_edits, password_changed_at, last_collection_export_at "
                "FROM users WHERE id = :user_id",
                {"user_id": session["user_id"]},
            )
            result = _user_row(user)
            if result is not None:
                changed_at = result.get("password_changed_at")
                if changed_at is not None and session["created_at"] < changed_at:
                    await UserService.delete_session(session_id)
                    return None
            return result
        except Exception as exc:
            logger.error("Error getting user by session {}: {}", session_id, exc)
            return None
