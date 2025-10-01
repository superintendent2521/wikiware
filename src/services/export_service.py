"""
Data export service for WikiWare collections.
Provides utilities to bundle collections and enforce per-user rate limits.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional

from bson import ObjectId
from loguru import logger
from zipstream.aiozipstream import AioZipStream

from ..database import (
    db_instance,
    get_branches_collection,
    get_history_collection,
    get_pages_collection,
    get_users_collection,
    get_image_hashes_collection,
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
    ZIP_CHUNK_SIZE = 64 * 1024

    @staticmethod
    async def _ensure_user(username: str) -> Dict[str, Any]:
        """Fetch the user document or raise if it does not exist."""
        user = await UserService.get_user_by_username(username)
        if not user:
            raise ValueError(f"User '{username}' not found or database unavailable")
        return user

    @classmethod
    async def _check_rate_limit(
        cls, user: Dict[str, Any]
    ) -> tuple[bool, datetime | None]:
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

    @staticmethod
    def build_export_filename(timestamp: Optional[datetime] = None) -> str:
        """Create a timestamped filename for the export archive."""
        timestamp = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        return f"wikiware-collections-{timestamp}.zip"

    @classmethod
    async def _stream_collection_json(
        cls, collection, label: str
    ) -> AsyncIterator[bytes]:
        """Yield a JSON array representation of the collection without buffering everything."""
        try:
            cursor = collection.find(limit=cls.MAX_FETCH)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(f"Failed to create cursor for {label} collection: {exc}")
            raise ExportUnavailableError(
                f"Failed to stream {label} collection"
            ) from exc

        first = True
        count = 0
        yield b"["
        try:
            async for document in cursor:
                serialized = {
                    key: cls._serialize_value(value) for key, value in document.items()
                }
                encoded = json.dumps(
                    serialized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if first:
                    first = False
                    yield encoded
                else:
                    yield b"," + encoded
                count += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(f"Failed while streaming {label} collection: {exc}")
            raise ExportUnavailableError(
                f"Failed to stream {label} collection"
            ) from exc
        else:
            if count == cls.MAX_FETCH:
                logger.warning(
                    f"Reached MAX_FETCH limit ({cls.MAX_FETCH}) for {label} collection. Export may be incomplete."  # pragma: no cover - logging only
                )
            yield b"]"
            logger.info(
                f"Streamed {count} documents from {label} collection"  # pragma: no cover - logging only
            )

    @classmethod
    async def generate_export_archive(
        cls,
        username: str,
        *,
        filename: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """Stream a ZIP archive containing pages, history, picture_shas, and branches collections."""
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
        image_collection = get_image_hashes_collection() # its just sha256s
        missing_collections = [
            name
            for name, coll in (
                ("pages", pages_collection),
                ("history", history_collection),
                ("branches", branches_collection),
                ("images", image_collection)
            )
            if coll is None
        ]
        if missing_collections:
            raise ExportUnavailableError(
                f"{', '.join(missing_collections)} collection(s) are unavailable"
            )

        archive_name = filename or cls.build_export_filename()
        sources = [
            {
                "stream": cls._stream_collection_json(pages_collection, "pages"),
                "name": "pages.json",
                "compression": "deflate",
            },
            {
                "stream": cls._stream_collection_json(history_collection, "history"),
                "name": "history.json",
                "compression": "deflate",
            },
                        {
                "stream": cls._stream_collection_json(image_collection, "picture_shas"),
                "name": "image.json",
                "compression": "deflate",
            },
            {
                "stream": cls._stream_collection_json(branches_collection, "branches"),
                "name": "branches.json",
                "compression": "deflate",
            },
        ]

        archive = AioZipStream(sources, chunksize=cls.ZIP_CHUNK_SIZE)
        stream_completed = False

        try:
            async for chunk in archive.stream():
                yield chunk
            stream_completed = True
        finally:
            if stream_completed:
                await cls._update_last_export(username)
                logger.info(
                    f"Collections export streamed for user {username} with archive {archive_name}"  # pragma: no cover - logging only
                )


__all__ = ["ExportService", "ExportRateLimitError", "ExportUnavailableError"]
