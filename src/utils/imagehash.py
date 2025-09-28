"""
Image hash service for WikiWare.
Calculates SHA256 sums for images and stores them in the database.
"""

import hashlib
from pathlib import Path
from typing import Dict, List

from loguru import logger

from ..database import get_image_hashes_collection
from .images import _list_images
from ..config import UPLOAD_DIR


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    hash_sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except FileNotFoundError:
        logger.warning(f"File not found: {file_path}")
        return ""
    except Exception as e:
        logger.error(f"Error calculating hash for {file_path}: {e}")
        return ""


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
        file_path = Path(UPLOAD_DIR) / filename
        if not file_path.exists():
            logger.warning(f"Image file does not exist: {file_path}")
            continue

        sha256 = calculate_sha256(file_path)
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
                }
            },
            upsert=True,
        )
        logger.info(f"Updated hash for {filename}: {sha256}")

    logger.info("Image hash update completed")
