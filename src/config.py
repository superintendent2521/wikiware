"""
Configuration module for WikiWare.
Centralizes all configuration settings and environment variables.
"""

import os
from urllib.parse import unquote, urlparse
from dotenv import load_dotenv
from .env_config import SECRET_KEY, ACCESS_KEY  # Importing sensitive data

# Load environment variables
load_dotenv()


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
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")


def _database_name_from_mongodb_url(mongodb_url):
    """Return the database path from a MongoDB URI, if one is present."""
    db_path = urlparse(mongodb_url).path.lstrip("/")
    if not db_path:
        return None
    return unquote(db_path.split("/", 1)[0]) or None


MONGODB_DB_NAME = (
    os.getenv("MONGODB_DB_NAME")
    or os.getenv("MONGODB_DB")
    or _database_name_from_mongodb_url(MONGODB_URL)
    or "wikiware"
)

# Application settings
APP_TITLE = "WikiWare"
APP_DESCRIPTION = "A simple wiki software"

# File upload settings
UPLOAD_DIR = "static/uploads"
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]

# Object storage (S3-compatible) settings
S3_ENDPOINT = "s3.iad.xny.onl"  # e.g. "https://s3.xenyth.example"
S3_ACCESS_KEY = ACCESS_KEY  # Imported from env_config.py
S3_SECRET_KEY = SECRET_KEY  # Imported from env_config.py
S3_BUCKET = "wikiware"
S3_FORCE_PATH_STYLE = True
S3_PUBLIC_URL = ""  # Optional public CDN/base URL override

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
NAME = "Starship Wiki"

# Version shown on all pages

VERSION = "2.6"

# Template settings
TEMPLATE_DIR = "templates"

# Static files settings
STATIC_DIR = "static"
HELP_STATIC_DIR = "templates/help"

# Session cookie settings
# Set SESSION_COOKIE_SECURE=true when the app is served exclusively over HTTPS.
SESSION_COOKIE_SECURE = _parse_bool_env("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_NAME = "__Host-user_session" if SESSION_COOKIE_SECURE else "user_session"
