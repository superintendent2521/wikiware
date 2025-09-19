"""
Service for managing site-wide settings such as the global announcement banner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from ..database import db_instance


@dataclass(frozen=True)
class Banner:
    message: str
    level: str = "info"
    is_active: bool = False

    @property
    def should_display(self) -> bool:
        """Return True if the banner has content and is marked active."""
        return self.is_active and bool(self.message.strip())


_DEFAULT_BANNER = Banner(message="", level="info", is_active=False)
_ALLOWED_LEVELS = {"info", "success", "warning", "danger"}


class SettingsService:
    """Provide helpers for reading and writing global site settings."""

    _banner_cache: Banner = _DEFAULT_BANNER

    @staticmethod
    def _normalize_level(level: Optional[str]) -> str:
        if not level:
            return "info"
        level = level.lower().strip()
        if level not in _ALLOWED_LEVELS:
            logger.warning(
                "Attempted to set unsupported banner level '%s'; defaulting to 'info'",
                level,
            )
            return "info"
        return level

    @classmethod
    async def get_banner(cls) -> Banner:
        """Fetch the current banner details, using the cache if offline."""
        if not db_instance.is_connected:
            return cls._banner_cache

        settings_collection = db_instance.get_collection("settings")
        if settings_collection is None:
            return cls._banner_cache

        doc = await settings_collection.find_one({"_id": "global_banner"})
        if not doc:
            cls._banner_cache = _DEFAULT_BANNER
            return cls._banner_cache

        banner = Banner(
            message=doc.get("message", ""),
            level=cls._normalize_level(doc.get("level")),
            is_active=bool(doc.get("is_active", False)),
        )
        cls._banner_cache = banner
        return banner

    @classmethod
    async def update_banner(
        cls, *, message: str, level: Optional[str] = None, is_active: bool = False
    ) -> bool:
        """Persist new banner settings and refresh the cache."""
        if not db_instance.is_connected:
            logger.error("Cannot update banner while database is offline")
            return False

        settings_collection = db_instance.get_collection("settings")
        if settings_collection is None:
            logger.error("Settings collection is unavailable")
            return False

        normalized_level = cls._normalize_level(level)
        payload = {
            "message": message,
            "level": normalized_level,
            "is_active": bool(is_active) and bool(message.strip()),
        }

        await settings_collection.update_one(
            {"_id": "global_banner"},
            {"$set": payload},
            upsert=True,
        )

        cls._banner_cache = Banner(**payload)
        logger.info("Updated global banner; active=%s", cls._banner_cache.is_active)
        return True

    @classmethod
    async def clear_banner(cls) -> bool:
        """Disable the banner and clear message content."""
        return await cls.update_banner(message="", level="info", is_active=False)


__all__ = ["SettingsService", "Banner"]
