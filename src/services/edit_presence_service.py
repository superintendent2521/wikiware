"""
Edit presence service for coordinating real-time editing sessions.

Provides helpers to create short-lived presence leases backed by MongoDB TTL
documents, update heartbeats, and fetch current roster data for a page/branch.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from loguru import logger

from ..database import db_instance


LeaseDoc = Dict[str, object]
Roster = Dict[str, List[Dict[str, str]]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


class EditPresenceService:
    """Manage edit presence leases stored in MongoDB."""

    DEFAULT_LEASE_SECONDS = 90
    MAX_EXTENSION_AHEAD_SECONDS = 120
    HEARTBEAT_THROTTLE_SECONDS = 5

    _heartbeat_guard: Dict[str, datetime] = {}

    @classmethod
    def _collection(cls):
        return db_instance.get_collection("edit_sessions")

    @classmethod
    def _normalize_branch(cls, branch: Optional[str]) -> str:
        value = (branch or "main").strip()
        return value or "main"

    @classmethod
    def _normalize_mode(cls, mode: Optional[str]) -> str:
        normalized = (mode or "edit").strip().lower()
        if normalized not in {"edit", "view"}:
            return "edit"
        return normalized

    @classmethod
    async def _prune_expired(
        cls, *, page: Optional[str] = None, branch: Optional[str] = None
    ) -> int:
        """Eagerly delete expired leases to keep rosters accurate."""
        if not db_instance.is_connected:
            return 0

        collection = cls._collection()
        if collection is None:
            return 0

        now = _utcnow()
        query: Dict[str, object] = {"lease_expires_at": {"$lte": now}}
        if page:
            query["page"] = page
        if branch:
            query["branch"] = branch

        try:
            result = await collection.delete_many(query)
            return int(result.deleted_count)
        except Exception as exc:  # IGNORE W0718
            logger.warning("Failed to prune expired edit sessions: {}", exc)
            return 0

    @classmethod
    async def create_session(
        cls,
        *,
        page: str,
        branch: str,
        mode: str,
        client_id: str,
        user_id: str,
        username: str,
    ) -> Tuple[Optional[str], Optional[datetime], Optional[Roster], Optional[str]]:
        """Create a new presence lease and return the roster."""
        if not db_instance.is_connected:
            return None, None, None, "offline"

        collection = cls._collection()
        if collection is None:
            return None, None, None, "collection_unavailable"

        branch_normalized = cls._normalize_branch(branch)
        mode_normalized = cls._normalize_mode(mode)
        now = _utcnow()

        await cls._prune_expired(page=page, branch=branch_normalized)

        duplicate = await collection.find_one(
            {
                "user_id": user_id,
                "client_id": client_id,
                "page": page,
                "branch": branch_normalized,
                "lease_expires_at": {"$gt": now},
            }
        )
        if duplicate:
            return None, None, None, "duplicate"

        session_id = secrets.token_urlsafe(12)
        lease_expires_at = now + timedelta(seconds=cls.DEFAULT_LEASE_SECONDS)

        doc: LeaseDoc = {
            "page": page,
            "branch": branch_normalized,
            "user_id": user_id,
            "username": username,
            "mode": mode_normalized,
            "session_id": session_id,
            "client_id": client_id,
            "created_at": now,
            "last_heartbeat": now,
            "lease_expires_at": lease_expires_at,
        }

        await collection.insert_one(doc)
        roster = await cls.get_roster(page=page, branch=branch_normalized)
        return session_id, lease_expires_at, roster, None

    @classmethod
    async def touch_heartbeat(
        cls, *, session_id: str, user_id: str, page: str, branch: str
    ) -> Tuple[str, Optional[datetime]]:
        """Extend a lease on ping; returns status and new expiry when extended."""
        if not db_instance.is_connected:
            return "offline", None

        collection = cls._collection()
        if collection is None:
            return "collection_unavailable", None

        now = _utcnow()
        last_ping = cls._heartbeat_guard.get(session_id)
        if last_ping and (now - last_ping).total_seconds() < cls.HEARTBEAT_THROTTLE_SECONDS:
            return "throttled", None

        doc = await collection.find_one(
            {
                "session_id": session_id,
                "user_id": user_id,
                "page": page,
                "branch": cls._normalize_branch(branch),
            }
        )
        if not doc:
            return "missing", None

        lease_expires_at = _coerce_datetime(doc.get("lease_expires_at"))
        if not lease_expires_at or lease_expires_at <= now:
            await collection.delete_one({"_id": doc.get("_id")})
            return "expired", None

        base = lease_expires_at if lease_expires_at > now else now
        target = base + timedelta(seconds=cls.DEFAULT_LEASE_SECONDS)
        max_allowed = now + timedelta(seconds=cls.MAX_EXTENSION_AHEAD_SECONDS)
        new_expiry = min(target, max_allowed)

        await collection.update_one(
            {"_id": doc.get("_id")},
            {
                "$set": {
                    "last_heartbeat": now,
                    "lease_expires_at": new_expiry,
                }
            },
        )
        cls._heartbeat_guard[session_id] = now
        return "extended", new_expiry

    @classmethod
    async def release_session(cls, *, session_id: str, user_id: str) -> bool:
        """Expire a lease early."""
        if not db_instance.is_connected:
            return False

        collection = cls._collection()
        if collection is None:
            return False

        cls._heartbeat_guard.pop(session_id, None)
        result = await collection.delete_one(
            {"session_id": session_id, "user_id": user_id}
        )
        return result.deleted_count > 0

    @classmethod
    async def get_roster(
        cls, *, page: str, branch: str
    ) -> Optional[Roster]:
        """Return active editors for the page/branch."""
        if not db_instance.is_connected:
            return None

        collection = cls._collection()
        if collection is None:
            return None

        await cls._prune_expired(page=page, branch=branch)

        now = _utcnow()
        cursor = collection.find(
            {
                "page": page,
                "branch": cls._normalize_branch(branch),
                "mode": "edit",
                "lease_expires_at": {"$gt": now},
            },
            {"_id": 0, "username": 1, "client_id": 1, "mode": 1},
        )

        editors: List[Dict[str, str]] = []

        async for row in cursor:
            entry = {
                "username": str(row.get("username", "")),
                "client_id": str(row.get("client_id", "")),
            }
            editors.append(entry)

        return {"editors": editors}

    @classmethod
    async def validate_session(
        cls, *, session_id: str, user_id: str, page: str, branch: str, mode: str
    ) -> Optional[LeaseDoc]:
        """Validate a provided edit session id matches the user/page/branch/mode."""
        if not db_instance.is_connected:
            return None

        collection = cls._collection()
        if collection is None:
            return None

        doc = await collection.find_one(
            {
                "session_id": session_id,
                "user_id": user_id,
                "page": page,
                "branch": cls._normalize_branch(branch),
                "mode": cls._normalize_mode(mode),
            }
        )
        if not doc:
            return None

        lease_expires_at = _coerce_datetime(doc.get("lease_expires_at"))
        if lease_expires_at is None or lease_expires_at <= _utcnow():
            await collection.delete_one({"_id": doc.get("_id")})
            return None

        return doc

    @classmethod
    async def get_session(cls, *, session_id: str, user_id: str) -> Optional[LeaseDoc]:
        """Fetch a lease by session/user without validating branch or mode."""
        if not db_instance.is_connected:
            return None

        collection = cls._collection()
        if collection is None:
            return None

        doc = await collection.find_one(
            {
                "session_id": session_id,
                "user_id": user_id,
            }
        )
        if not doc:
            return None

        lease_expires_at = _coerce_datetime(doc.get("lease_expires_at"))
        if lease_expires_at is None or lease_expires_at <= _utcnow():
            await collection.delete_one({"_id": doc.get("_id")})
            return None

        return doc

    @classmethod
    async def attach_presence_context(
        cls, user_id: str, page: str, branch: str, session_id: str
    ) -> Dict[str, str]:
        """Return lightweight presence context for logging/analytics."""
        if not db_instance.is_connected:
            return {}

        collection = cls._collection()
        if collection is None:
            return {}

        doc = await collection.find_one(
            {
                "session_id": session_id,
                "user_id": user_id,
                "page": page,
                "branch": cls._normalize_branch(branch),
                "lease_expires_at": {"$gt": _utcnow()},
            },
            {"_id": 0, "session_id": 1, "client_id": 1, "mode": 1},
        )
        if not doc:
            return {}

        return {
            "edit_session_id": str(doc.get("session_id", "")),
            "client_id": str(doc.get("client_id", "")),
            "mode": str(doc.get("mode", "")),
        }


__all__ = ["EditPresenceService"]
