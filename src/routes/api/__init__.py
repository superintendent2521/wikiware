"""
API routes package for WikiWare.
This package contains API route modules that return JSON/data responses.
"""

from . import logs, stats, images, exports, uploads, pdf, history, favorites

__all__ = ["logs", "stats", "images", "exports", "uploads", "pdf", "history", "admin", "favorites"]
