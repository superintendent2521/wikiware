"""
User service layer for WikiWare.
Contains business logic for user operations.
"""

import secrets
from datetime import timedelta
from typing import Optional, Dict, Any, Tuple, List, Set
from datetime import datetime, timezone
from passlib.context import CryptContext
from loguru import logger
from ..database import get_users_collection, db_instance
from ..models.user import UserRegistration

# Password hashing context
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


class UserService:
    """Service class for user-related operations."""

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using Argon2id (preferred) or bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password
        """
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.

        Args:
            plain_password: Plain text password
            hashed_password: Hashed password

        Returns:
            True if password matches, False otherwise
        """
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
        """
        Get a user by username.

        Args:
            username: Username to search for

        Returns:
            User document or None if not found
        """
        try:
            if not db_instance.is_connected:
                logger.warning(f"Database not connected - cannot get user: {username}")
                return None

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return None

            user = await users_collection.find_one({"username": username})
            return user
        except Exception as e:
            logger.error(f"Error getting user {username}: {str(e)}")
            return None

    @staticmethod
    async def list_favorites(username: str) -> Optional[List[Dict[str, str]]]:
        """
        Return the sanitized favorites list for a user.

        Args:
            username: Username whose favorites should be returned

        Returns:
            List of favorites as dictionaries with title/branch keys, or None if lookup failed
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    "Database not connected - cannot list favorites for user: %s",
                    username,
                )
                return None

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return None

            user_doc = await users_collection.find_one(
                {"username": username}, {"favorites": 1, "_id": 0}
            )
            if user_doc is None:
                logger.warning(f"User not found while listing favorites: {username}")
                return None

            raw_favorites = user_doc.get("favorites") or []
            normalized: List[Dict[str, str]] = []
            seen: Set[Tuple[str, str]] = set()
            mutated = False

            for entry in raw_favorites:
                if isinstance(entry, dict):
                    title = entry.get("title")
                    if not title:
                        mutated = True
                        continue
                    branch = (entry.get("branch") or "main").strip() or "main"
                    key = (title, branch)
                    if key in seen:
                        mutated = True
                        continue
                    if branch != entry.get("branch"):
                        mutated = True
                    seen.add(key)
                    normalized.append({"title": title, "branch": branch})
                elif isinstance(entry, str):
                    key = (entry, "main")
                    if key in seen:
                        mutated = True
                        continue
                    seen.add(key)
                    normalized.append({"title": entry, "branch": "main"})
                    mutated = True
                else:
                    mutated = True

            if mutated:
                await users_collection.update_one(
                    {"username": username},
                    {"$set": {"favorites": normalized}},
                )

            return normalized
        except Exception as e:
            logger.error(
                "Error listing favorites for user %s: %s",
                username,
                str(e),
            )
            return None

    @staticmethod
    async def add_favorite(
        username: str, title: str, branch: str = "main"
    ) -> bool:
        """
        Add a favorite page for the specified user.

        Args:
            username: Username to update
            title: Page title to favorite
            branch: Branch name for the page

        Returns:
            True if the operation succeeded or favorite already exists, False otherwise
        """
        try:
            favorites = await UserService.list_favorites(username)
            if favorites is None:
                return False

            if any(
                fav["title"] == title and fav["branch"] == branch
                for fav in favorites
            ):
                return True

            if not db_instance.is_connected:
                logger.warning(
                    "Database not connected - cannot add favorite for user: %s",
                    username,
                )
                return False

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return False

            favorite_entry = {"title": title, "branch": branch}
            update_result = await users_collection.update_one(
                {"username": username},
                {"$addToSet": {"favorites": favorite_entry}},
            )

            if update_result.matched_count == 0:
                logger.warning(
                    "User not found while adding favorite: %s",
                    username,
                )
                return False

            logger.info(
                "Added favorite '%s' (branch: %s) for user '%s'",
                title,
                branch,
                username,
            )
            return True
        except Exception as e:
            logger.error(
                "Error adding favorite '%s' (branch: %s) for user %s: %s",
                title,
                branch,
                username,
                str(e),
            )
            return False

    @staticmethod
    async def remove_favorite(
        username: str, title: str, branch: str = "main"
    ) -> bool:
        """
        Remove a favorite page for the specified user.

        Args:
            username: Username to update
            title: Page title to remove
            branch: Branch name for the page

        Returns:
            True if the operation succeeded or favorite was already missing, False otherwise
        """
        try:
            favorites = await UserService.list_favorites(username)
            if favorites is None:
                return False

            if not any(
                fav["title"] == title and fav["branch"] == branch
                for fav in favorites
            ):
                return True

            if not db_instance.is_connected:
                logger.warning(
                    "Database not connected - cannot remove favorite for user: %s",
                    username,
                )
                return False

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return False

            favorite_entry = {"title": title, "branch": branch}
            update_result = await users_collection.update_one(
                {"username": username},
                {"$pull": {"favorites": favorite_entry}},
            )

            # Remove legacy string-only entries if present
            await users_collection.update_one(
                {"username": username},
                {"$pull": {"favorites": title}},
            )

            if update_result.matched_count == 0:
                logger.warning(
                    "User not found while removing favorite: %s",
                    username,
                )
                return False

            logger.info(
                "Removed favorite '%s' (branch: %s) for user '%s'",
                title,
                branch,
                username,
            )
            return True
        except Exception as e:
            logger.error(
                "Error removing favorite '%s' (branch: %s) for user %s: %s",
                title,
                branch,
                username,
                str(e),
            )
            return False

    @staticmethod
    async def create_user(user_data: UserRegistration) -> Optional[Dict[str, Any]]:
        """
        Create a new user.

        Args:
            user_data: User registration data

        Returns:
            Created user document or None if failed
        """
        try:
            if not db_instance.is_connected:
                logger.error("Database not connected - cannot create user")
                return None

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return None

            # Check if username already exists
            existing_user = await UserService.get_user_by_username(user_data.username)
            if existing_user:
                logger.warning(f"Username already exists: {user_data.username}")
                return None

            # Hash password
            hashed_password = UserService.hash_password(user_data.password)

            # Create user document
            user_doc = {
                "username": user_data.username,
                "password_hash": hashed_password,
                "created_at": datetime.now(timezone.utc),
                "is_active": True,
                "is_admin": False,
                "favorites": [],
            }

            # Insert user
            result = await users_collection.insert_one(user_doc)
            user_doc["_id"] = result.inserted_id

            logger.info(f"User created: {user_data.username}")
            return user_doc
        except Exception as e:
            logger.error(f"Error creating user {user_data.username}: {str(e)}")
            return None

    @staticmethod
    async def authenticate_user(
        username: str,
        password: str,
        client_ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> Optional[Dict[str, Any]]:
        """
        Authenticate a user by username and password.

        Args:
            username: Username
            password: Plain text password

        Returns:
            User document if authentication successful, None otherwise
        """
        try:
            # Get user
            user = await UserService.get_user_by_username(username)
            if not user:
                logger.warning(f"User not found: {username}")
                logger.warning(
                    f"Failed login attempt: username={username}, ip={client_ip}, user_agent={user_agent}"
                )  # pylint: disable=C0301
                return None

            # Check if user is active
            if not user.get("is_active", True):
                logger.warning(f"User account is inactive: {username}")
                logger.warning(
                    f"Failed login attempt: username={username}, ip={client_ip}, user_agent={user_agent}"
                )  # pylint: disable=C0301
                return None

            # Verify password
            if not UserService.verify_password(password, user["password_hash"]):
                logger.warning(f"Invalid password for user: {username}")
                logger.warning(
                    f"Failed login attempt: username={username}, ip={client_ip}, user_agent={user_agent}"
                )  # pylint: disable=C0301
                return None

            logger.info(f"User authenticated: {username}")
            return user
        except Exception as e:
            logger.error(f"Error authenticating user {username}: {str(e)}")
            return None

    @staticmethod
    async def change_password(
        username: str,
        current_password: str,
        new_password: str,
    ) -> Tuple[bool, str]:
        """Change a user's password after verifying the current password."""
        try:
            if not db_instance.is_connected:
                logger.error("Database not connected - cannot change password")
                return False, "offline"

            users_collection = get_users_collection()
            if users_collection is None:
                logger.error("Users collection not available")
                return False, "users_collection_missing"

            user = await UserService.get_user_by_username(username)
            if not user:
                logger.warning(f"User not found for password change: {username}")
                return False, "user_not_found"

            if not UserService.verify_password(current_password, user["password_hash"]):
                logger.warning(f"Invalid current password for user: {username}")
                return False, "invalid_current_password"

            new_hash = UserService.hash_password(new_password)

            result = await users_collection.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "password_hash": new_hash,
                        "password_changed_at": datetime.now(timezone.utc),
                    }
                },
            )

            if result.modified_count == 1 or result.matched_count == 1:
                logger.info(f"Password updated for user: {username}")
                return True, ""

            logger.error(f"Failed to update password for user: {username}")
            return False, "update_failed"
        except Exception as e:
            logger.error(f"Error changing password for {username}: {str(e)}")
            return False, "error"

    @staticmethod
    async def create_session(user_id: str) -> Optional[str]:
        """
        Create a new session for a user.

        Args:
            user_id: User ID

        Returns:
            Session ID if successful, None otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.error("Database not connected - cannot create session")
                return None

            sessions_collection = db_instance.get_collection("sessions")
            if sessions_collection is None:
                logger.error("Sessions collection not available")
                return None

            # Generate a secure random session ID
            session_id = secrets.token_urlsafe(32)

            # Create session document
            session_doc = {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc).replace(
                    second=0, microsecond=0
                )
                + timedelta(hours=24),
            }

            # Insert session
            await sessions_collection.insert_one(session_doc)

            logger.info(f"Session created for user: {user_id}")
            return session_id
        except Exception as e:
            logger.error(f"Error creating session for user {user_id}: {str(e)}")
            return None

    @staticmethod
    async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a session by session ID.

        Args:
            session_id: Session ID

        Returns:
            Session document or None if not found or expired
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot get session: {session_id}"
                )
                return None

            sessions_collection = db_instance.get_collection("sessions")
            if sessions_collection is None:
                logger.error("Sessions collection not available")
                return None

            session = await sessions_collection.find_one({"session_id": session_id})
            if not session:
                return None

            # Normalize expires_at to timezone-aware UTC for safe comparison
            expires_at = session.get("expires_at")
            if isinstance(expires_at, datetime):
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = None

            now_utc = datetime.now(timezone.utc)
            if expires_at and expires_at > now_utc:
                return session

            # Delete expired session
            if session:
                await sessions_collection.delete_one({"session_id": session_id})
                logger.info(f"Expired session deleted: {session_id}")

            return None
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {str(e)}")
            return None

    @staticmethod
    async def delete_session(session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session ID

        Returns:
            True if successful, False otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot delete session: {session_id}"
                )
                return False

            sessions_collection = db_instance.get_collection("sessions")
            if sessions_collection is None:
                logger.error("Sessions collection not available")
                return False

            result = await sessions_collection.delete_one({"session_id": session_id})
            logger.info(f"Session deleted: {session_id}")
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {str(e)}")
            return False

    @staticmethod
    async def get_user_by_session(session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a user by session ID.

        Args:
            session_id: Session ID

        Returns:
            User document or None if not found
        """
        try:
            # Get session
            session = await UserService.get_session(session_id)
            if not session:
                return None

            # Get user
            user = await UserService.get_user_by_username(session["user_id"])
            return user
        except Exception as e:
            logger.error(f"Error getting user by session {session_id}: {str(e)}")
            return None
