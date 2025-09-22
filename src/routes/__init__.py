"""
Routes package for WikiWare.
This package contains all the route modules for the application.
"""

# Import route modules for easier access
from .api import (
    admin_router as api_admin,
    auth_router as api_auth,
    branches_router as api_branches,
    exports_router as api_exports,
    pages_router as api_pages,
    stats_router as api_stats,
    uploads_router as api_uploads,
    user_router as api_user,
)
from .web import (
    admin_router as web_admin,
    auth_router as web_auth,
    branches_router as web_branches,
    exports_router as web_exports,
    pages_router as web_pages,
    search_router as web_search,
    stats_router as web_stats,
    user_router as web_user,
)
from . import (
    history,
    logs,
    images,
)

__all__ = [
    "api_admin",
    "api_auth",
    "api_branches",
    "api_exports",
    "api_pages",
    "api_stats",
    "api_uploads",
    "api_user",
    "web_admin",
    "web_auth",
    "web_branches",
    "web_exports",
    "web_pages",
    "web_search",
    "web_stats",
    "web_user",
    "history",
    "logs",
    "images",
]
