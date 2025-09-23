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


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(
    request: Request, response: Response, csrf_protect: CsrfProtect = Depends()
):
    """
    Admin panel endpoint.
    Only accessible to users with is_admin=True.
    """
    user = await AuthMiddleware.get_current_user(request)
    if not user or not user.get("is_admin", False):
        # Redirect to login or home if not admin
        return HTMLResponse(
            content="<h1>Access Denied</h1><p>You must be an admin to view this page.</p>",
            status_code=403,
        )
    # Fetch all users for admin view
    users_collection = get_users_collection()
    if users_collection is None:
        users = []
    else:
        users_cursor = users_collection.find(
            {}, {"username": 1, "is_admin": 1, "is_active": 1, "created_at": 1}
        )
        users = await users_cursor.to_list(length=None)
    # Get statistics
    stats = await get_stats()
    # Get recent logs (last 5)
    recent_logs = await LogUtils.get_paginated_logs(1, 5)
    banner = await SettingsService.get_banner()
    # CSRF token for templates (logout form in base.html)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    template = templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "users": users,
            "total_edits": stats["total_edits"],
            "total_pages": stats["total_pages"],
            "total_characters": stats["total_characters"],
            "recent_logs": recent_logs["items"],
            "user": user,
            "csrf_token": csrf_token,
            "offline": not db_instance.is_connected,
            "banner": banner,
            "banner_levels": ["info", "success", "warning", "danger"],
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


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
