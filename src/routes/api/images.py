"""
Image API routes for WikiWare.
Provides API endpoints for image operations.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import asyncio

from ...middleware.auth_middleware import AuthMiddleware
from ...services.storage_service import (
    delete_image as storage_delete_image,
    image_exists as storage_image_exists,
    StorageError,
)
from ...utils.images import _list_images
from ...utils.logs import log_action

router = APIRouter()


@router.get("/images", response_class=JSONResponse)
async def list_images_api(request: Request):
    """Return JSON list of images; requires authentication."""
    await AuthMiddleware.require_auth(request)
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _list_images)
    return JSONResponse(content={"items": items})


@router.delete("/images/{filename}")
async def delete_image(
    filename: str,
    request: Request,
):
    """Delete an image by filename; requires admin privileges."""
    # Require admin authentication

    user = await AuthMiddleware.get_current_user(request)
    if not user or not user.get("is_admin", False):
        # Redirect to login or home if not admin
        raise HTTPException(
            status_code=403,
            detail="Access Denied: You must be an admin to perform this action.",
        )
    
    # Security check - prevent directory traversal
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    loop = asyncio.get_running_loop()

    try:
        exists = await loop.run_in_executor(None, storage_image_exists, filename)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="Failed to access image storage") from exc

    if not exists:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        await loop.run_in_executor(None, storage_delete_image, filename)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="Failed to delete image") from exc

    await log_action(
        user["username"],
        "delete_image",
        f"Deleted image: {filename}",
        "images",
    )

    return JSONResponse(
        status_code=200,
        content={"message": f"Image '{filename}' deleted successfully"},
    )
