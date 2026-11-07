"""
Admin panel routes for WikiWare.
Only accessible to users with admin: true flag.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.analytics_service import AnalyticsService
from ...services.settings_service import SettingsService
from ...services.user_service import UserService
from ...stats import get_stats
from ...utils.logs import get_paginated_logs
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
    users = await UserService.list_users()
    # Get statistics
    stats = await get_stats()
    analytics = await AnalyticsService.get_admin_dashboard_metrics()
    # Get recent logs (last 5)
    recent_logs = await get_paginated_logs(1, 5)
    banner = await SettingsService.get_banner(force_refresh=True)
    feature_flags = await SettingsService.get_feature_flags(force_refresh=True)
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
            "feature_flags": feature_flags,
            "analytics": analytics,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template
