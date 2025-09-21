"""
Authentication routes for WikiWare.
Handles user registration, login, and logout operations.
"""

from fastapi import APIRouter, Request, Form, Response, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from datetime import datetime, timezone
from ..utils.validation import sanitize_redirect_path
from ..services.user_service import UserService
from ..models.user import UserRegistration
from ..config import DEV, SESSION_COOKIE_NAME
from ..database import db_instance
from ..utils.template_env import get_templates

from loguru import logger

router = APIRouter()

templates = get_templates()

@router.get("/register", response_class=HTMLResponse)
async def register_form(
    request: Request, response: Response, csrf_protect: CsrfProtect = Depends()
):
    """Show user registration form."""
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    logger.debug("Generated CSRF token for registration form")
    logger.debug("Generated signed CSRF token for registration form")
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
@router.post("/register")
async def register_user(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_protect: CsrfProtect = Depends(),
):
    """Handle user registration."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)
        if not db_instance.is_connected:
            logger.error("Database not connected - cannot register user")
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "Registration is temporarily unavailable",
                    "offline": True,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Check if passwords match
        if password != confirm_password:
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "Passwords do not match",
                    "username": username,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Create user registration model
        user_data = UserRegistration(username=username, password=password)
        # Create user
        user = await UserService.create_user(user_data)
        if not user:
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "Username already exists",
                    "username": username,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Create session
        session_id = await UserService.create_session(user["username"])
        if not session_id:
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "Failed to create session",
                    "username": username,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Set secure session cookie
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            secure=not DEV,  # Set to False in development mode
            httponly=True,
            samesite="Lax",
            path="/",
            max_age=3600 * 24 * 7,  # 1 week
        )
        logger.info(f"User registered and logged in: {username}")
        return response
    except Exception as e:
        logger.error(f"Error registering user {username}: {str(e)}")
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        template = templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "An error occurred during registration",
                "username": username,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
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
@router.post("/login")
async def login_user(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Handle user login."""
    try:
        # Validate CSRF token
        await csrf_protect.validate_csrf(request)
        safe_next = sanitize_redirect_path(next)
        # Extract client IP and User-Agent for logging
        xff = request.headers.get("x-forwarded-for")
        client_ip = (
            xff.split(",")[0].strip()
            if xff
            else (request.client.host if request.client else "unknown")
        )
        user_agent = request.headers.get("user-agent", "unknown")
        if not db_instance.is_connected:
            logger.error(f"Database not connected - cannot login user: {username} | {client_ip} | {user_agent} | db_offline | {request.url.path}")
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Login is temporarily unavailable",
                    "offline": True,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Authenticate user
        user = await UserService.authenticate_user(
            username, password, client_ip=client_ip, user_agent=user_agent
        )
        if not user:
            # Log failure using unified logger (also writes to file via loguru config)
            logger.warning(f"Login failed: {username} | {client_ip} | {user_agent} | failure | {request.url.path}")
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Invalid username or password",
                    "username": username,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Create session
        session_id = await UserService.create_session(user["username"])
        if not session_id:
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            template = templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Failed to create session",
                    "username": username,
                    "csrf_token": csrf_token,
                },
            )
            csrf_protect.set_csrf_cookie(signed_token, template)
            return template
        # Set secure session cookie
        response = RedirectResponse(url=safe_next, status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            secure=not DEV,  # Set to False in development mode
            httponly=True,
            samesite="Lax",
            path="/",
            max_age=3600 * 24 * 7,  # 1 week
        )
        # Log successful login using unified logger (also writes to file via loguru config)
        logger.info(f"User logged in: {username} | {client_ip} | {user_agent} | success | {request.url.path}")
        return response
    except Exception as e:
        logger.error(f"Error logging in user {username}: {str(e)}")
        # Log error using unified logger (also writes to file via loguru config)
        logger.error(f"Login error: {username} | {client_ip} | {user_agent} | error | {request.url.path}")
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        template = templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "An error occurred during login",
                "username": username,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template
@router.post("/logout")
async def logout_user(
    request: Request, response: Response, csrf_protect: CsrfProtect = Depends()
):
    """Handle user logout."""
    try:
        # Get session ID from cookie
        session_id = (
            request.cookies.get(SESSION_COOKIE_NAME)
            or request.cookies.get("__Host-user_session")
            or request.cookies.get("user_session")
        )
        # Delete session from database if it exists
        if session_id:
            await UserService.delete_session(session_id)
        # Clear session cookie
        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            secure=not DEV,  # Set to False in development mode
            httponly=True,
            samesite="Lax",
            path="/",
        )
        logger.info("User logged out")
        return response
    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
        response = RedirectResponse(url="/", status_code=303)
        return response
