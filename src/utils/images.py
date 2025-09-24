from pathlib import Path
from typing import Dict, List

from ..config import UPLOAD_DIR

from loguru import logger

def _list_images() -> List[Dict]:
    """Return a list of image file metadata from the uploads directory."""
    upload_path = Path(UPLOAD_DIR)
    if not upload_path.exists():
        return []
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
    items: List[Dict] = []
    for entry in upload_path.iterdir():
        if entry.is_file() and entry.suffix.lower() in image_extensions:
            try:
                stat = entry.stat()
                items.append(
                    {
                        "filename": entry.name,
                        "url": f"/static/uploads/{entry.name}",
                        "size": stat.st_size,
                        "modified": int(stat.st_mtime),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to stat image {entry}: {e}")
                continue
    # Sort by most recently modified first
    items.sort(key=lambda x: x["modified"], reverse=True)
    return items