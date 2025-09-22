"""
Export routes for WikiWare.
Provides endpoints to download database collections (web interface).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...middleware.auth_middleware import AuthMiddleware
from ...utils.template_env import get_templates

router = APIRouter()
templates = get_templates()


@router.get("/exports", response_class=HTMLResponse)
async def export_collections_page(
    request: Request, csrf_protect: CsrfProtect = Depends()
):
    """Render the collections export confirmation page with rate-limit warning."""
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    template = templates.TemplateResponse(
        "exports.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "offline": False,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template
