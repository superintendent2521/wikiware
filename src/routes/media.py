"""
Media proxy routes for serving uploaded images.
"""

import asyncio
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from loguru import logger

from ..services.storage_service import StorageError, download_image_bytes

router = APIRouter()


@router.get("/media/uploads/{filename}")
async def serve_uploaded_image(filename: str):
    """Proxy stored images so S3 objects can be previewed without public endpoints."""
    if "/" in filename or ".." in filename or filename.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid filename")
    try:
        data = await download_image_bytes(filename)
    except StorageError as exc:
        logger.warning(f"Failed to retrieve image '{filename}': {exc}")
        raise HTTPException(status_code=404, detail="Image not found") from exc

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type)
