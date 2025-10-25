"""
Service for managing site-wide settings such as the global announcement banner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from ..database import db_instance


@dataclass(frozen=True)
class Banner:
    """Represent the global announcement banner settings."""

    message: str
    level: str = "info"
    is_active: bool = False
    expires_at: Optional[datetime] = None
    duration_hours: Optional[int] = None

    @property
    def is_expired(self) -> bool:
        """Return True once the banner has reached its expiration time."""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def should_display(self) -> bool:
        """Return True if the banner has content, is active, and not expired."""
        if not self.is_active or not self.message.strip():
            return False
        return not self.is_expired


_DEFAULT_BANNER = Banner(
    message="",
    level="info",
    is_active=False,
    expires_at=None,
    duration_hours=None,
)
_ALLOWED_LEVELS = {"info", "success", "warning", "danger"}


@dataclass(frozen=True)
class FeatureFlags:
    """Represent global feature toggles exposed in the admin panel."""

    page_editing_enabled: bool = True
    account_creation_enabled: bool = True
    image_upload_enabled: bool = True


_DEFAULT_FEATURE_FLAGS = FeatureFlags()


class SettingsService:
    """Provide helpers for reading and writing global site settings."""

    _banner_cache: Banner = _DEFAULT_BANNER
    _feature_flags_cache: FeatureFlags = _DEFAULT_FEATURE_FLAGS

    @staticmethod
    def _normalize_level(level: Optional[str]) -> str:
        if not level:
            return "info"
        level = level.lower().strip()
        if level not in _ALLOWED_LEVELS:
            logger.warning(
                f"Attempted to set unsupported banner level '{level}'; defaulting to 'info'"
            )
            return "info"
        return level

    @staticmethod
    def _parse_expires_at(value: Optional[object]) -> Optional[datetime]:
        if not value:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                logger.warning(f"Could not parse banner expiration '{value}'")
                return None

            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        logger.warning(f"Unsupported expires_at type '{type(value)!r}'")
        return None

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

        expires_at = cls._parse_expires_at(doc.get("expires_at"))
        raw_duration = doc.get("duration_hours")
        duration_hours: Optional[int] = None
        if raw_duration is not None:
            try:
                duration_hours = int(raw_duration)
            except (TypeError, ValueError):
                logger.warning(f"Could not parse banner duration '{raw_duration}'")
                duration_hours = None

        is_active = bool(doc.get("is_active", False))

        if expires_at and is_active and datetime.now(timezone.utc) >= expires_at:
            logger.info("Global banner expired; marking as inactive")
            await settings_collection.update_one(
                {"_id": "global_banner"},
                {"$set": {"is_active": False, "duration_hours": None}},
            )
            is_active = False
            duration_hours = None

        if not is_active:
            duration_hours = None

        banner = Banner(
            message=doc.get("message", ""),
            level=cls._normalize_level(doc.get("level")),
            is_active=is_active,
            expires_at=expires_at,
            duration_hours=duration_hours,
        )
        cls._banner_cache = banner
        return banner

    @classmethod
    async def update_banner(
        cls,
        *,
        message: str,
        level: Optional[str] = None,
        is_active: bool = False,
        expires_in_hours: Optional[int] = None,
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
        trimmed_message = message.strip()
        active_flag = bool(is_active) and bool(trimmed_message)

        expires_at: Optional[datetime] = None
        duration_hours_value: Optional[int] = None
        if active_flag and expires_in_hours:
            try:
                duration_hours_value = int(expires_in_hours)
                if duration_hours_value > 0:
                    expires_at = datetime.now(timezone.utc) + timedelta(
                        hours=duration_hours_value
                    )
                else:
                    logger.warning(
                        f"Ignoring non-positive banner expiration value: {expires_in_hours!r}"
                    )
                    duration_hours_value = None
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignoring invalid banner expiration value: {expires_in_hours!r}"
                )
                duration_hours_value = None
                expires_at = None

        payload = {
            "message": trimmed_message,
            "level": normalized_level,
            "is_active": active_flag,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "duration_hours": duration_hours_value,
        }

        await settings_collection.update_one(
            {"_id": "global_banner"},
            {"$set": payload},
            upsert=True,
        )

        cls._banner_cache = Banner(
            message=payload["message"],
            level=payload["level"],
            is_active=payload["is_active"],
            expires_at=expires_at,
            duration_hours=duration_hours_value,
        )
        logger.info(f"Updated global banner; active={cls._banner_cache.is_active}")
        return True

    @classmethod
    async def clear_banner(cls) -> bool:
        """Disable the banner and clear message content."""
        return await cls.update_banner(
            message="",
            level="info",
            is_active=False,
            expires_in_hours=None,
        )

    @classmethod
    async def get_feature_flags(cls) -> FeatureFlags:
        """Return the current set of feature toggles, cached when offline."""
        if not db_instance.is_connected:
            return cls._feature_flags_cache

        settings_collection = db_instance.get_collection("settings")
        if settings_collection is None:
            return cls._feature_flags_cache

        doc = await settings_collection.find_one({"_id": "feature_flags"})
        if not doc:
            cls._feature_flags_cache = _DEFAULT_FEATURE_FLAGS
            return cls._feature_flags_cache

        flags = FeatureFlags(
            page_editing_enabled=doc.get("page_editing_enabled", True),
            account_creation_enabled=doc.get("account_creation_enabled", True),
            image_upload_enabled=doc.get("image_upload_enabled", True),
        )
        cls._feature_flags_cache = flags
        return flags

    @classmethod
    async def update_feature_flags(
        cls,
        *,
        page_editing_enabled: bool,
        account_creation_enabled: bool,
        image_upload_enabled: bool,
    ) -> bool:
        """Persist feature toggle values and refresh the cache."""
        if not db_instance.is_connected:
            logger.error("Cannot update feature flags while database is offline")
            return False

        settings_collection = db_instance.get_collection("settings")
        if settings_collection is None:
            logger.error("Settings collection is unavailable")
            return False

        payload = {
            "page_editing_enabled": page_editing_enabled,
            "account_creation_enabled": account_creation_enabled,
            "image_upload_enabled": image_upload_enabled,
        }

        await settings_collection.update_one(
            {"_id": "feature_flags"},
            {"$set": payload},
            upsert=True,
        )

        cls._feature_flags_cache = FeatureFlags(**payload)
        logger.info(f"Updated feature flags: {cls._feature_flags_cache}")
        return True


__all__ = ["SettingsService", "Banner", "FeatureFlags"]
