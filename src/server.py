"""
Main FastAPI application for WikiWare.
This is the refactored, modular version with separated concerns.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi_csrf_protect import CsrfProtect
from pydantic import BaseModel
from .config import APP_TITLE, APP_DESCRIPTION, STATIC_DIR, TEMPLATE_DIR, DEV
from .database import init_database
from .routes import pages, search, history, branches, uploads, stats, logs, auth, admin, images
# Remove TableExtension import since it's not available in this version
from loguru import logger
import os

# Configure loguru
os.makedirs("logs", exist_ok=True)
logger.add("logs/wikiware.log", rotation="1 day", retention="7 days", level="INFO")
logger.add("logs/errors.log", rotation="1 day", retention="7 days", level="ERROR")

class CsrfSettings(BaseModel):
    secret_key: str = "asecretkeythatisverylongandsecure"
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
    settings = CsrfSettings()
    logger.info(
        f"CSRF config: secure={settings.cookie_secure}, httponly={settings.httponly}, samesite={settings.cookie_samesite}, key={settings.cookie_key}"
    )
    return settings

# Create FastAPI app
app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include route modules
app.include_router(pages.router)
app.include_router(search.router)
app.include_router(history.router)
app.include_router(branches.router)
app.include_router(uploads.router)
app.include_router(stats.router)
app.include_router(logs.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(images.router)

# Startup event
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
