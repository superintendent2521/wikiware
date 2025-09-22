"""
Routes package for WikiWare.
This package contains all the route modules for the application.
"""

# Import route modules for easier access
from .api import admin
from . import (
    pages,
    search,
    history,
    branches,
    uploads,
    stats,
    logs,
    images,
    user,
    exports,
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
]
