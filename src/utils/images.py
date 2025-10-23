from typing import Dict, List

from loguru import logger

from ..services.storage_service import StorageError, list_images as storage_list_images


async def _list_images() -> List[Dict]:
    """Return a list of image file metadata from the storage backend."""
    try:
        return await storage_list_images()
    except StorageError as exc:
        logger.error("Failed to list images from storage: %s", exc)
        return []
