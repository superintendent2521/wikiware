"""
Data export service for WikiWare collections.
Provides utilities to bundle collections and enforce per-user rate limits.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional

from loguru import logger
from sqlalchemy import text
from zipstream.aiozipstream import AioZipStream

from ..database import db_instance
from ..services.user_service import UserService
from ..utils.sql import execute, execute_returning


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
        now_utc = datetime.now(timezone.utc)
        await execute(
            "UPDATE users SET last_collection_export_at = :timestamp "
            "WHERE username = :username",
            {"timestamp": now_utc, "username": username},
        )

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Convert database values into JSON-serializable ones."""
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
    async def _reserve_export(cls, username: str) -> datetime:
        """Claim the cooldown atomically so simultaneous requests cannot bypass it."""
        now = datetime.now(timezone.utc)
        try:
            row = await execute_returning(
                "UPDATE users SET last_collection_export_at = :now "
                "WHERE username = :username AND (last_collection_export_at IS NULL "
                "OR last_collection_export_at <= :cutoff) RETURNING id",
                {"username": username, "now": now, "cutoff": now - cls.EXPORT_INTERVAL},
            )
            if row is None:
                user = await cls._ensure_user(username)
                last = user.get("last_collection_export_at")
                if last is None:
                    raise ExportUnavailableError("Unable to reserve export")
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                raise ExportRateLimitError(last + cls.EXPORT_INTERVAL)
            return now
        except (ExportRateLimitError, ExportUnavailableError):
            raise
        except Exception as exc:
            raise ExportUnavailableError("Unable to reserve export") from exc

    @classmethod
    async def _stream_collection_json(cls, connection, statement: str, label: str) -> AsyncIterator[bytes]:
        """Stream every row from the archive's shared repeatable-read snapshot."""
        first = True
        count = 0
        yield b"["
        try:
            async with connection.stream(text(statement)) as result:
                async for document in result.mappings():
                    serialized = {key: cls._serialize_value(value) for key, value in document.items()}
                    encoded = json.dumps(serialized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    yield encoded if first else b"," + encoded
                    first = False
                    count += 1
        except Exception as exc:
            logger.error("Failed while streaming {}: {}", label, exc)
            raise ExportUnavailableError(f"Failed to stream {label}") from exc
        yield b"]"
        logger.info("Streamed {} rows from {}", count, label)

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

        reservation = await cls._reserve_export(username)
        stream_completed = False
        try:
            async with db_instance.engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    sources = [
                        {
                            "stream": cls._stream_collection_json(
                                connection,
                                "SELECT p.title, b.name AS branch, b.content, b.author_name AS author, "
                                "b.edit_summary, b.edit_permission, b.created_at, b.updated_at, "
                                "COALESCE((SELECT jsonb_agg(u.username ORDER BY u.username) "
                                "FROM branch_editors e JOIN users u ON u.id = e.user_id "
                                "WHERE e.page_branch_id = b.id), '[]'::jsonb) AS allowed_users "
                                "FROM page_branches b JOIN pages p ON p.id = b.page_id ORDER BY b.id",
                                "pages",
                            ),
                            "name": "pages.json",
                            "compression": "deflate",
                        },
                        {
                            "stream": cls._stream_collection_json(
                                connection,
                                "SELECT p.title, b.name AS branch, r.revision_number, r.content, "
                                "r.author_name AS author, r.edited_by, r.edit_summary, r.created_at "
                                "FROM revisions r JOIN page_branches b ON b.id = r.page_branch_id "
                                "JOIN pages p ON p.id = b.page_id ORDER BY r.id",
                                "history",
                            ),
                            "name": "history.json",
                            "compression": "deflate",
                        },
                        {
                            "stream": cls._stream_collection_json(
                                connection,
                                "SELECT filename, sha256, size, last_modified AS modified, url "
                                "FROM image_hashes ORDER BY id",
                                "picture_shas",
                            ),
                            "name": "image.json",
                            "compression": "deflate",
                        },
                        {
                            "stream": cls._stream_collection_json(
                                connection,
                                "SELECT p.title AS page_title, b.name AS branch_name, b.created_from, "
                                "b.content, b.author_name AS author, b.created_at, b.updated_at "
                                "FROM page_branches b JOIN pages p ON p.id = b.page_id ORDER BY b.id",
                                "branches",
                            ),
                            "name": "branches.json",
                            "compression": "deflate",
                        },
                    ]

                    archive = AioZipStream(sources, chunksize=cls.ZIP_CHUNK_SIZE)
                    async for chunk in archive.stream():
                        yield chunk
            stream_completed = True
        except ExportUnavailableError:
            raise
        except Exception as exc:
            raise ExportUnavailableError("Failed to create a consistent export") from exc
        finally:
            if not stream_completed:
                try:
                    await execute(
                        "UPDATE users SET last_collection_export_at = :previous "
                        "WHERE username = :username AND last_collection_export_at = :reservation",
                        {"username": username, "previous": user.get("last_collection_export_at"), "reservation": reservation},
                    )
                except Exception as exc:
                    logger.warning("Failed to release interrupted export reservation: {}", exc)
            else:
                logger.info("Collections export streamed for user {}", username)



__all__ = ["ExportService", "ExportRateLimitError", "ExportUnavailableError"]
