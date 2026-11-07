"""
Configuration module for WikiWare.
Centralizes all configuration settings and environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def _parse_bool_env(name, default):
    """Parse an optional boolean environment variable."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, 1/0, yes/no, or on/off")


# Server configuration
PORT = int(os.getenv("PORT", "8000"))
DEV = _parse_bool_env("DEV", False)
HOST = os.getenv("HOST", "0.0.0.0")

# Database configuration
# ``DATABASE_URL`` is the application connection string.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/wikiware"
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://") :]
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "5"))
DATABASE_MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "5"))
DATABASE_POOL_TIMEOUT = int(os.getenv("DATABASE_POOL_TIMEOUT", "30"))
DATABASE_POOL_RECYCLE = int(os.getenv("DATABASE_POOL_RECYCLE", "1800"))
DATABASE_CONNECT_RETRIES = int(os.getenv("DATABASE_CONNECT_RETRIES", "3"))

# Application settings
APP_TITLE = os.getenv("APP_TITLE", "WikiWare")
APP_DESCRIPTION = os.getenv("APP_DESCRIPTION", "A simple wiki software")

# File upload settings
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(PROJECT_DIR / "static" / "uploads"))
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]

# Object storage (S3-compatible) settings
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "").strip()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "wikiware")
S3_FORCE_PATH_STYLE = _parse_bool_env("S3_FORCE_PATH_STYLE", True)
S3_PUBLIC_URL = os.getenv("S3_PUBLIC_URL", "").strip()
CSRF_SECRET_KEY = os.getenv("CSRF_SECRET_KEY", "")
CSRF_COOKIE_SECURE = _parse_bool_env("CSRF_COOKIE_SECURE", False)

# Logging settings
# UNUSED
LOG_DIR = "logs"
LOG_RETENTION = "7 days"
LOG_LEVEL = "INFO"
# USED!
# DONT ACTIVATE IN PROD! IT SLOWS THINGS DOWN
DB_QUERY_LOGGING_ENABLED = False
REQUEST_LOGGING_ENABLED = False

# Name shown on all pages
NAME = os.getenv("WIKI_NAME", "Starship Wiki")

# Version shown on all pages

VERSION = "2.6"

# Template settings
TEMPLATE_DIR = str(PROJECT_DIR / "templates")

# Static files settings
STATIC_DIR = str(PROJECT_DIR / "static")
HELP_STATIC_DIR = str(PROJECT_DIR / "templates" / "help")

# Session cookie settings
# Set SESSION_COOKIE_SECURE=true when the app is served exclusively over HTTPS.
SESSION_COOKIE_SECURE = _parse_bool_env("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_NAME = "__Host-user_session" if SESSION_COOKIE_SECURE else "user_session"


def validate_runtime_config() -> None:
    """Fail startup on incomplete storage or unsafe production configuration."""
    storage_values = (S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY)
    if any(storage_values) and not all(storage_values):
        raise ValueError("Set S3_ENDPOINT, S3_ACCESS_KEY, and S3_SECRET_KEY together")
    if all(storage_values) and not S3_BUCKET:
        raise ValueError("S3_BUCKET must not be empty when S3 is enabled")
    if not DATABASE_URL.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    if not 1 <= PORT <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    if not DEV:
        if len(CSRF_SECRET_KEY) < 32:
            raise ValueError("Set CSRF_SECRET_KEY to at least 32 characters in production")
        if not SESSION_COOKIE_SECURE or not CSRF_COOKIE_SECURE:
            raise ValueError("Enable SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE in production")
