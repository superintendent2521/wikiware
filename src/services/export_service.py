"""
Data export service for WikiWare collections.
Provides utilities to bundle collections and enforce per-user rate limits.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Tuple

from bson import ObjectId
from loguru import logger

from ..database import (
    db_instance,
    get_branches_collection,
    get_history_collection,
    get_pages_collection,
    get_users_collection,
)
from ..services.user_service import UserService


class ExportRateLimitError(Exception):
    """Raised when an export is attempted before the cooldown expires."""

    def __init__(self, next_allowed: datetime):
        self.next_allowed = next_allowed
        super().__init__(
            f"Export available again after {next_allowed.replace(microsecond=0).isoformat()}"
        )


class ExportUnavailableError(Exception):
    """Raised when exports are not available due to database issues."""


class ExportService:
    """Service for exporting wiki collections for download."""

    EXPORT_INTERVAL = timedelta(hours=24)
    MAX_FETCH = 250_000  # safeguard to avoid unbounded cursor to_list calls

    @staticmethod
    async def _ensure_user(username: str) -> Dict[str, Any]:
        """Fetch the user document or raise if it does not exist."""
        user = await UserService.get_user_by_username(username)
        if not user:
            raise ValueError(f"User '{username}' not found or database unavailable")
        return user

    @classmethod
    async def _check_rate_limit(cls, user: Dict[str, Any]) -> Tuple[bool, datetime | None]:
        """Return True if export is allowed, along with the stored timestamp."""
        last_export = user.get("last_collection_export_at")
        if isinstance(last_export, datetime):
            if last_export.tzinfo is None:
                last_export = last_export.replace(tzinfo=timezone.utc)
            if last_export + cls.EXPORT_INTERVAL > datetime.now(timezone.utc):
                return False, last_export
        return True, last_export if isinstance(last_export, datetime) else None

    @classmethod
    async def _update_last_export(cls, username: str) -> None:
        """Persist the timestamp of the most recent export for the user."""
        users_collection = get_users_collection()
        if users_collection is None:
            raise ExportUnavailableError("Users collection unavailable")
        now_utc = datetime.now(timezone.utc)
        await users_collection.update_one(
            {"username": username},
            {"$set": {"last_collection_export_at": now_utc}},
        )

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Convert MongoDB-specific/complex values into JSON-serializable ones."""
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        if isinstance(value, dict):
            return {k: ExportService._serialize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [ExportService._serialize_value(v) for v in value]
        return value

    @classmethod
    def _serialize_documents(cls, documents: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        """Yield serialized documents ready for JSON dumping."""
        for doc in documents:
            yield {key: cls._serialize_value(value) for key, value in doc.items()}

    @classmethod
    async def _fetch_collection(cls, collection, label: str) -> Iterable[Dict[str, Any]]:
        """Fetch all documents from the provided collection."""
        try:
            documents = await collection.find().to_list(length=cls.MAX_FETCH)
            logger.info(f"Fetched {len(documents)} documents from {label} collection")
            return documents
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(f"Failed to fetch {label} collection: {exc}")
            raise ExportUnavailableError(f"Failed to fetch {label} collection") from exc

    @classmethod
    async def generate_export_archive(cls, username: str) -> Tuple[bytes, str]:
        """Generate a ZIP archive containing pages, history, and branches collections.

        Returns:
            Tuple of (archive_bytes, filename)

        Raises:
            ExportRateLimitError: if the user has exported within the cooldown period.
            ExportUnavailableError: if required collections are unavailable.
        """
        if not db_instance.is_connected:
            raise ExportUnavailableError("Database connection is not available")

        user = await cls._ensure_user(username)
        allowed, last_export = await cls._check_rate_limit(user)
        if not allowed and last_export is not None:
            next_allowed = last_export + cls.EXPORT_INTERVAL
            raise ExportRateLimitError(next_allowed)

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()
        branches_collection = get_branches_collection()

        missing_collections = [
            name
            for name, coll in (
                ("pages", pages_collection),
                ("history", history_collection),
                ("branches", branches_collection),
            )
            if coll is None
        ]

        if missing_collections:
            raise ExportUnavailableError(
                f"{', '.join(missing_collections)} collection(s) are unavailable"
            )

        pages_data = await cls._fetch_collection(pages_collection, "pages")
        history_data = await cls._fetch_collection(history_collection, "history")
        branches_data = await cls._fetch_collection(branches_collection, "branches")

        in_memory = io.BytesIO()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"wikiware-collections-{timestamp}.zip"

        with zipfile.ZipFile(in_memory, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "pages.json",
                json.dumps(list(cls._serialize_documents(pages_data)), ensure_ascii=True),
            )
            archive.writestr(
                "history.json",
                json.dumps(list(cls._serialize_documents(history_data)), ensure_ascii=True),
            )
            archive.writestr(
                "branches.json",
                json.dumps(list(cls._serialize_documents(branches_data)), ensure_ascii=True),
            )

        await cls._update_last_export(username)
        in_memory.seek(0)
        logger.info(
            f"Collections export generated for user {username} with archive size {in_memory.getbuffer().nbytes} bytes"
        )
        return in_memory.read(), filename


__all__ = ["ExportService", "ExportRateLimitError", "ExportUnavailableError"]
