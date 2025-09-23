"""
Routes package for WikiWare.
This package contains all the route modules for the application.
"""

# Import route modules for easier access
from .api import logs, stats as api_stats, images as api_images, exports as api_exports, uploads
from .web import (
    pages,
    search,
    history,
    branches,
    stats,
    admin,
    images,
    user,
    exports,
    auth,
)

__all__ = [
    "pages",
    "search",
    "history",
    "branches",
    "uploads",
    "stats",
    "logs",
    "admin",
    "images",
    "user",
    "exports",
    "auth",
    "api_stats",
    "api_images",
    "api_exports",
]
