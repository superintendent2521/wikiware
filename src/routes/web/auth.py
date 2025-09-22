"""
Authentication routes for WikiWare.
Handles user registration and login forms (web interface).
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...config import DEV, SESSION_COOKIE_NAME
from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...utils.template_env import get_templates
from ...utils.validation import sanitize_redirect_path

router = APIRouter()

templates = get_templates()


@router.get("/register", response_class=HTMLResponse)
async def register_form(
    request: Request, response: Response, csrf_protect: CsrfProtect = Depends()
):
    """Show user registration form."""
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    logger.debug("Generated CSRF token for registration form")
    # Attach CSRF cookie to the actual response being returned
    template = templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "offline": not db_instance.is_connected,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    logger.debug("CSRF cookie attached to registration response")
    return template


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    response: Response,
    next: str = "/",
    csrf_protect: CsrfProtect = Depends(),
):
    """Show login form."""
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    safe_next = sanitize_redirect_path(next)
    template = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "offline": not db_instance.is_connected,
            "csrf_token": csrf_token,
            "next": safe_next,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/account/password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
):
    """Display the password change form for authenticated users."""
    user = await AuthMiddleware.get_current_user(request)
    if not user:
        logger.debug("Unauthenticated user attempted to access password change form")
        return RedirectResponse(url="/login?next=/account/password", status_code=303)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    template = templates.TemplateResponse(
        "password_reset.html",
        {
            "request": request,
            "offline": not db_instance.is_connected,
            "csrf_token": csrf_token,
            "user": user,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template
