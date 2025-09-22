from .admin import router as admin_router
from .auth import router as auth_router
from .branches import router as branches_router
from .exports import router as exports_router
from .pages import router as pages_router
from .stats import router as stats_router
from .uploads import router as uploads_router
from .user import router as user_router

__all__ = [
    "admin_router",
    "auth_router",
    "branches_router",
    "exports_router",
    "pages_router",
    "stats_router",
    "uploads_router",
    "user_router",
]
