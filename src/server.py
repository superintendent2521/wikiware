"""
Main FastAPI application for WikiWare.
This is the refactored, modular version with separated concerns.
"""

import os
import secrets
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi_csrf_protect import CsrfProtect
from pydantic import BaseModel
from loguru import logger
from .config import NAME, APP_DESCRIPTION, STATIC_DIR, DEV, HELP_STATIC_DIR
from .database import init_database
from .routes.web import (
    pages,
    search,
    history,
    branches,
    stats,
    auth,
    admin,
    images,
    user,
    exports,
)
from .routes.api import (
    stats as api_stats,
    images as api_images,
    exports as api_exports,
    pdf as api_pdf,
)
from .services import log_streamer
from .services.settings_service import SettingsService
from .middleware.security_headers import SecurityHeadersMiddleware
from .utils.template_env import get_templates

# Configure loguru
os.makedirs("logs", exist_ok=True)
logger.add("logs/wikiware.log", rotation="1 day", retention="7 days", level="INFO")
logger.add("logs/errors.log", rotation="1 day", retention="7 days", level="ERROR")

_CSRF_SECRET = os.getenv("CSRF_SECRET_KEY")
if not _CSRF_SECRET:
    _CSRF_SECRET = secrets.token_urlsafe(64)
    logger.warning(
        "CSRF_SECRET_KEY not set; generated ephemeral secret key for this process"
    )


class CsrfSettings(BaseModel):
    secret_key: str
    cookie_samesite: str = "lax"
    # Use env override so local HTTP works by default; set CSRF_COOKIE_SECURE=true in prod
    cookie_secure: bool = os.getenv("CSRF_COOKIE_SECURE", "false").lower() == "true"
    httponly: bool = True
    cookie_key: str = "fastapi-csrf-token"
    # Read CSRF tokens from request body/forms so standard HTML forms work
    # Library expects 'body' (not 'form') for this setting
    token_location: str = "body"
    header_name: str = "X-CSRF-Token"
    # Keep token_key for forms elsewhere if needed (ignored when token_location='header')
    token_key: str = "csrf_token"


@CsrfProtect.load_config
def get_csrf_config():
    settings = CsrfSettings(secret_key=_CSRF_SECRET)
    logger.info(
        f"CSRF config: secure={settings.cookie_secure}, httponly={settings.httponly}"
        f", samesite={settings.cookie_samesite}, key={settings.cookie_key}"
    )
    return settings


# Create FastAPI app
app = FastAPI(title=NAME, description=APP_DESCRIPTION)


templates = get_templates()
logger.info(f"Wiki Name is {NAME}  ")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/help", StaticFiles(directory=HELP_STATIC_DIR), name="help")

# Security headers middleware
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def inject_global_banner(request: Request, call_next):
    """Attach the global announcement banner to request state for templates."""
    try:
        banner = await SettingsService.get_banner()
    except Exception as exc:
        logger.error(f"Failed to load global banner: {exc}")
        banner = SettingsService._banner_cache
    request.state.global_banner = banner
    response = await call_next(request)
    return response


# Include route modules
app.include_router(pages.router)
app.include_router(search.router)
app.include_router(history.router)
app.include_router(branches.router)
app.include_router(stats.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(images.router)
app.include_router(user.router)
app.include_router(exports.router)
# API routes
app.include_router(api_stats.router, prefix="/api")
app.include_router(api_images.router, prefix="/api")
app.include_router(api_exports.router, prefix="/api")
app.include_router(api_pdf.router, prefix="/api")
# From Utils, because its a service, not a route
app.include_router(log_streamer.router)
# Initilize log streaming.
log_streamer.setup_log_streaming(app, add_file_sink=False)


@app.on_event("startup")
async def startup_event():
    try:
        await init_database()
        logger.info("WikiWare application started successfully")
    except Exception as e:
        logger.error(f"Error during application startup: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    from .config import HOST, PORT, DEV

    uvicorn.run(app, host=HOST, port=PORT, reload=DEV)
