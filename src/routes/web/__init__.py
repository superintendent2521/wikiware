"""
Web routes package for WikiWare.
This package contains web route modules that return HTML template responses.
"""

from . import (
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
    "stats",
    "admin",
    "images",
    "user",
    "exports",
    "auth",
]
