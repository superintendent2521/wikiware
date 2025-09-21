"""
Image library routes for WikiWare.
Allows authenticated users to browse and search uploaded images.
"""

from fastapi import APIRouter, Request, Depends, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi_csrf_protect import CsrfProtect
from pathlib import Path
from loguru import logger
from typing import List, Dict
from ..config import UPLOAD_DIR
from ..middleware.auth_middleware import AuthMiddleware
from ..database import db_instance
from ..utils.template_env import get_templates

router = APIRouter()

templates = get_templates()


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


@router.get("/images", response_class=HTMLResponse)
async def images_library(
    request: Request,
    response: Response,
    q: str = "",
    csrf_protect: CsrfProtect = Depends(),
):
    """Serve the image library page with optional filename filtering via `q`."""
    # Require authentication (any logged-in user)
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    items = _list_images()
    if q:
        q_lower = q.lower()
        items = [i for i in items if q_lower in i["filename"].lower()]
    template = templates.TemplateResponse(
        "images.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "offline": not db_instance.is_connected,
            "query": q,
            "images": items,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/api/images", response_class=JSONResponse)
async def list_images_api(request: Request):
    """Return JSON list of images; requires authentication."""
    await AuthMiddleware.require_auth(request)
    return JSONResponse(content={"items": _list_images()})
