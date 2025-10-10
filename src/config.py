"""
Configuration module for WikiWare.
Centralizes all configuration settings and environment variables.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Server configuration
PORT = int(os.getenv("PORT", "8000"))
DEV = os.getenv("DEV", "false").lower() == "true"
HOST = os.getenv("HOST", "0.0.0.0")

# Database configuration
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

# Application settings
APP_TITLE = "WikiWare"
APP_DESCRIPTION = "A simple wiki software"

# File upload settings
UPLOAD_DIR = "static/uploads"
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]

# Object storage (S3-compatible) settings
S3_ENDPOINT = os.getenv("S3_ENDPOINT")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET", "wikiware")
S3_REGION = os.getenv("S3_REGION")
S3_FORCE_PATH_STYLE = os.getenv("S3_FORCE_PATH_STYLE", "true").lower() == "true"
S3_PUBLIC_URL = os.getenv("S3_PUBLIC_URL")

# Logging settings
LOG_DIR = "logs"
LOG_RETENTION = "7 days"
LOG_LEVEL = "INFO"

# Name shown on all pages
NAME = "Starship Wiki"

# Version shown on all pages

VERSION = "1.7"

# Template settings
TEMPLATE_DIR = "templates"

# Static files settings
STATIC_DIR = "static"
HELP_STATIC_DIR = "templates/help"

# Session cookie settings
# Use __Host- prefix only when cookies are Secure (i.e., in production/HTTPS).
SESSION_COOKIE_NAME = "__Host-user_session" if not DEV else "user_session"
