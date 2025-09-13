"""
Admin panel routes for WikiWare.
Only accessible to users with admin: true flag.
"""

from fastapi import APIRouter, Request, Depends, Response
from ..middleware.auth_middleware import AuthMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ..config import TEMPLATE_DIR
from ..services.user_service import UserService
from ..database import get_users_collection
from ..stats import get_stats
from ..utils.logs import LogUtils
from ..database import db_instance
from fastapi_csrf_protect import CsrfProtect

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)

@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, response: Response, csrf_protect: CsrfProtect = Depends()):
    """
    Admin panel endpoint.
    Only accessible to users with is_admin=True.
    """
    user = await AuthMiddleware.get_current_user(request)
    if not user or not user.get("is_admin", False):
        # Redirect to login or home if not admin
        return HTMLResponse(content="<h1>Access Denied</h1><p>You must be an admin to view this page.</p>", status_code=403)
    
    # Fetch all users for admin view
    users_collection = get_users_collection()
    if users_collection is None:
        users = []
    else:
        users_cursor = users_collection.find({}, {"username": 1, "is_admin": 1, "is_active": 1, "created_at": 1})
        users = await users_cursor.to_list(length=None)
    
    # Get statistics
    stats = await get_stats()
    
    # Get recent logs (last 5)
    recent_logs = await LogUtils.get_paginated_logs(1, 5)

    # CSRF token for templates (logout form in base.html)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    template = templates.TemplateResponse("admin.html", {
        "request": request,
        "users": users,
        "total_edits": stats["total_edits"],
        "total_pages": stats["total_pages"],
        "total_characters": stats["total_characters"],
        "recent_logs": recent_logs["items"],
        "user": user,
        "csrf_token": csrf_token,
        "offline": not db_instance.is_connected
    })
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template
