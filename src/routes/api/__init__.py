"""
API routes package for WikiWare.
This package contains API route modules that return JSON/data responses.
"""

from . import logs, stats, images, exports, uploads

__all__ = ["logs", "stats", "images", "exports", "uploads"]
