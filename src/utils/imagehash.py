"""
Image hash service for WikiWare.
Calculates SHA256 sums for images and stores them in the database.
"""

import hashlib
from typing import Dict, List

from loguru import logger

from ..database import get_image_hashes_collection
from ..services.storage_service import StorageError, download_image_bytes
from .images import _list_images


def calculate_sha256(filename: str) -> str:
    """Calculate SHA256 hash for an uploaded image."""
    try:
        data = download_image_bytes(filename)
    except StorageError as exc:
        logger.warning(f"Failed to retrieve image {filename} for hashing: {exc}")
        return ""
    hash_sha256 = hashlib.sha256()
    hash_sha256.update(data)
    return hash_sha256.hexdigest()


def get_all_image_hashes() -> List[Dict]:
    """Retrieve all image hashes from the database."""
    collection = get_image_hashes_collection()
    if collection is None:
        logger.error("Image hashes collection not available")
        return []
    return list(collection.find({}))


async def update_image_hashes():
    """Calculate SHA256 hashes for all images and store in database."""
    collection = get_image_hashes_collection()
    if collection is None:
        logger.error("Image hashes collection not available")
        return

    images = _list_images()
    for image in images:
        filename = image["filename"]
        sha256 = calculate_sha256(filename)
        if not sha256:
            continue

        # Upsert the hash
        await collection.update_one(
            {"filename": filename},
            {
                "$set": {
                    "filename": filename,
                    "sha256": sha256,
                    "size": image["size"],
                    "modified": image["modified"],
                    "url": image.get("url"),
                }
            },
            upsert=True,
        )
        logger.info(f"Updated hash for {filename}: {sha256}")

    logger.info("Image hash update completed")
