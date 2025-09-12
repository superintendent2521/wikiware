"""
Main FastAPI application for WikiWare.
This is the refactored, modular version with separated concerns.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .config import APP_TITLE, APP_DESCRIPTION, STATIC_DIR, TEMPLATE_DIR
from .database import init_database
from .routes import pages, search, history, branches, uploads, stats
from loguru import logger
import os

# Configure loguru
os.makedirs("logs", exist_ok=True)
logger.add("logs/wikiware.log", rotation="1 day", retention="7 days", level="INFO")
logger.add("logs/errors.log", rotation="1 day", retention="7 days", level="ERROR")

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
