"""
Image library routes for WikiWare.
Allows authenticated users to browse and search uploaded images.
"""



from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger
import asyncio

from ...config import UPLOAD_DIR
from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...utils.template_env import get_templates
from ...utils.images import _list_images
from ...utils.
router = APIRouter()

templates = get_templates()


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
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _list_images)
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
