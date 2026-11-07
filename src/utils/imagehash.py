"""
Image hash service for WikiWare.
Calculates SHA256 sums for images and stores them in the database.
"""

import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from loguru import logger

from ..database import db_instance
from ..services.storage_service import StorageError, download_image_bytes
from .sql import execute, fetch_all, fetch_one
from .images import _list_images


async def calculate_sha256(filename: str) -> str:
    """Calculate SHA256 hash for an uploaded image."""
    try:
        data = await download_image_bytes(filename)
    except StorageError as exc:
        logger.warning("Failed to retrieve image {} for hashing: {}", filename, exc)
        return ""
    return hashlib.sha256(data).hexdigest()


async def get_all_image_hashes() -> List[Dict]:
    """Retrieve all image hashes from the database."""
    if not db_instance.is_connected:
        logger.error("Image hashes collection not available")
        return []
    return await fetch_all(
        "SELECT filename, sha256, size, last_modified AS modified, url FROM image_hashes ORDER BY filename"
    )


async def get_image_hash_by_sha256(sha256: str) -> Dict | None:
    """Return image metadata for a content hash."""
    if not db_instance.is_connected:
        return None
    return await fetch_one(
        "SELECT filename, sha256, size, last_modified AS modified, url FROM image_hashes WHERE sha256 = :sha256",
        {"sha256": sha256},
    )


async def save_image_hash(
    filename: str, sha256: str, size: int, modified: object, url: str | None
) -> bool:
    """Insert or update image metadata."""
    if not db_instance.is_connected:
        return False
    return await execute(
        "INSERT INTO image_hashes (filename, sha256, size, last_modified, url) "
        "VALUES (:filename, :sha256, :size, :modified, :url) "
        "ON CONFLICT (filename) DO UPDATE SET sha256 = EXCLUDED.sha256, "
        "size = EXCLUDED.size, last_modified = EXCLUDED.last_modified, url = EXCLUDED.url",
        {"filename": filename, "sha256": sha256, "size": size, "modified": modified, "url": url},
    ) >= 0


async def update_image_hashes() -> None:
    """Calculate SHA256 hashes for all images and store in database."""
    if not db_instance.is_connected:
        logger.error("Image hashes collection not available")
        return

    images = await _list_images()
    for image in images:
        filename = image.get("filename")
        if not filename:
            logger.warning("Skipping image hash update for metadata without filename")
            continue

        sha256 = await calculate_sha256(filename)
        if not sha256:
            continue

        modified = image.get("modified")
        if isinstance(modified, (int, float)):
            modified = datetime.fromtimestamp(modified, tz=timezone.utc)

        await execute(
            "INSERT INTO image_hashes (filename, sha256, size, last_modified, url) "
            "VALUES (:filename, :sha256, :size, :modified, :url) "
            "ON CONFLICT (filename) DO UPDATE SET sha256 = EXCLUDED.sha256, "
            "size = EXCLUDED.size, last_modified = EXCLUDED.last_modified, url = EXCLUDED.url",
            {
                "filename": filename,
                "sha256": sha256,
                "size": image.get("size", 0),
                "modified": modified,
                "url": image.get("url"),
            },
        )
        logger.info("Updated hash for {}: {}", filename, sha256)

    logger.info("Image hash update completed")
