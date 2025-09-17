"""
Validation utilities for WikiWare.
Contains common validation functions used across the application.
"""

import re
from typing import Optional
from urllib.parse import urlparse

_TITLE_PATTERN = re.compile(r"^[A-Za-z0-9 _\-]+$")
_BRANCH_PARAM_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_title(title: str) -> bool:
    """Validate title to prevent path traversal or other unsafe patterns."""
    if not title or not title.strip():
        return False

    # Disallow traversal and path-like prefixes
    if ".." in title or title.startswith(("/", "\\")):
        return False

    # Prevent attempts to inject schemes or special characters that break routing
    if any(ch in title for ch in (":", "?", "#")):
        return False

    return bool(_TITLE_PATTERN.fullmatch(title))


def is_valid_branch_name(branch_name: str) -> bool:
    """Validate branch name to ensure it's safe and follows naming conventions."""
    if not branch_name or not branch_name.strip():
        return False

    # Check for path traversal attempts
    if ".." in branch_name or "/" in branch_name or "\\" in branch_name:
        return False

    # Check for reserved names
    reserved_names = ["main", "master", "head", "origin"]
    if branch_name.lower() in reserved_names:
        return False

    return True


def is_safe_branch_parameter(branch: Optional[str]) -> bool:
    """Return True if the branch value is safe to echo in URLs."""
    if not branch:
        return False
    return bool(_BRANCH_PARAM_PATTERN.fullmatch(branch))


def sanitize_redirect_path(target: Optional[str], default: str = "/") -> str:
    """Ensure redirect targets stay within the current application."""
    if not target:
        return default

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default

    path = parsed.path or "/"

    if not path.startswith("/"):
        path = "/" + path.lstrip("/")

    if ".." in path.split("/"):
        return default

    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def sanitize_referer_url(
    current_url: str, referer: Optional[str], default: str = "/"
) -> str:
    """Sanitize a referer header so redirects remain on the same origin."""
    if not referer:
        return default

    referer = referer.strip()
    if not referer:
        return default

    referer_parsed = urlparse(referer)
    if referer_parsed.scheme or referer_parsed.netloc:
        current_parsed = urlparse(current_url)
        if (referer_parsed.scheme, referer_parsed.netloc) != (
            current_parsed.scheme,
            current_parsed.netloc,
        ):
            return default
        candidate = referer_parsed.path or "/"
        if referer_parsed.query:
            candidate = f"{candidate}?{referer_parsed.query}"
        return sanitize_redirect_path(candidate, default=default)

    return sanitize_redirect_path(referer, default=default)


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent security issues."""
    # Remove path separators and other dangerous characters
    dangerous_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    sanitized = filename

    for char in dangerous_chars:
        sanitized = sanitized.replace(char, "_")

    return sanitized
