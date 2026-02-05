from typing import Dict, List

from loguru import logger

from ..services.storage_service import StorageError, list_images

# why the fuck did i do this
async def _list_images() -> List[Dict]:
    """Return a list of image file metadata from the storage backend."""
    try:
        return await list_images()
    except StorageError as exc:
        logger.error(f"Failed to list images from storage: {exc}")
        return []
