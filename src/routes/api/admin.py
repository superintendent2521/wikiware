"""
Admin api routes.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from fastapi_csrf_protect import CsrfProtect

from ...middleware.auth_middleware import AuthMiddleware
from ...services.settings_service import SettingsService
router = APIRouter()


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
    duration_value = form.get("banner_duration", "").strip()

    expires_in_hours = None
    if duration_value in {"24", "48", "72"}:
        try:
            expires_in_hours = int(duration_value)
        except ValueError:
            expires_in_hours = None

    success = await SettingsService.update_banner(
        message=message,
        level=level,
        is_active=is_active,
        expires_in_hours=expires_in_hours,
    )
    status = "banner_saved" if success else "banner_error"
    redirect_url = request.url_for("admin_panel")
    return RedirectResponse(
        url=f"{redirect_url}?status={status}",
        status_code=303,
    )


@router.post("/admin/features")
async def update_feature_flags(request: Request, csrf_protect: CsrfProtect = Depends()):
    """Update global feature toggle settings from the admin panel."""
    form = await request.form()
    await csrf_protect.validate_csrf(request)
    user = await AuthMiddleware.require_auth(request)
    if not user.get("is_admin", False):
        return RedirectResponse(url="/", status_code=303)

    flags = {
        "page_editing_enabled": form.get("page_editing_enabled") == "on",
        "account_creation_enabled": form.get("account_creation_enabled") == "on",
        "image_upload_enabled": form.get("image_upload_enabled") == "on",
    }

    success = await SettingsService.update_feature_flags(**flags)
    status = "features_saved" if success else "features_error"
    redirect_url = request.url_for("admin_panel")
    return RedirectResponse(
        url=f"{redirect_url}?status={status}",
        status_code=303,
    )
