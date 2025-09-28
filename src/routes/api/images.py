"""
Image API routes for WikiWare.
Provides API endpoints for image operations.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import asyncio

from ...middleware.auth_middleware import AuthMiddleware
from ...utils.images import _list_images

router = APIRouter()


@router.get("/images", response_class=JSONResponse)
async def list_images_api(request: Request):
    """Return JSON list of images; requires authentication."""
    await AuthMiddleware.require_auth(request)
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _list_images)
    return JSONResponse(content={"items": items})
