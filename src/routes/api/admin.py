"""
Admin panel routes for WikiWare.
Only accessible to users with admin: true flag.
"""

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect

from ...database import db_instance, get_users_collection
from ...middleware.auth_middleware import AuthMiddleware
from ...services.settings_service import SettingsService
from ...stats import get_stats
from ...utils.logs import LogUtils
from ...utils.template_env import get_templates

router = APIRouter()

templates = get_templates()


@router.post("/admin/banner")
async def update_banner(request: Request, csrf_protect: CsrfProtect = Depends()):
    """Update the global banner message from the admin panel."""
    form = await request.form()
    await csrf_protect.validate_csrf(request)
    user = await AuthMiddleware.require_auth(request)
    if not user.get("is_admin", False):
        return RedirectResponse(url="/", status_code=303)
    message = form.get("banner_message", "").strip()
    level = form.get("banner_level", "info")
    is_active = form.get("banner_active") == "on"
    success = await SettingsService.update_banner(
        message=message,
        level=level,
        is_active=is_active,
    )
    status = "banner_saved" if success else "banner_error"
    redirect_url = request.url_for("admin_panel")
    return RedirectResponse(
        url=f"{redirect_url}?status={status}",
        status_code=303,
    )
