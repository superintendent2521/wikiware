from typing import Dict, List

from ..services.storage_service import list_images as storage_list_images


async def _list_images() -> List[Dict]:
    """Return a list of image file metadata from the storage backend."""
    return await storage_list_images()
