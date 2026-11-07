"""
Validation utilities for WikiWare.
Contains common validation functions used across the application.
"""

import re
from typing import Optional
from urllib.parse import unquote, urlparse

_TITLE_PATTERN = re.compile(r"^[A-Za-z0-9 _\-]+$")
_BRANCH_PARAM_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DANGEROUS_FILENAME_CHARS = re.compile(r'[\/\\:*?"<>|\x00-\x1f]')
_RESERVED_BRANCH_NAMES = {"main", "master", "head", "origin"}


def is_valid_title(title: str) -> bool:
    """Validate title to prevent path traversal or other unsafe patterns."""
    if not title or not title.strip() or len(title) > 255:
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
    if not branch_name or len(branch_name) > 100:
        return False

    normalized_branch = branch_name.strip()
    if not normalized_branch:
        return False

    if branch_name != normalized_branch:
        return False

    if normalized_branch.lower() in _RESERVED_BRANCH_NAMES:
        return False

    return bool(_BRANCH_PARAM_PATTERN.fullmatch(normalized_branch))


def is_safe_branch_parameter(branch: Optional[str]) -> bool:
    """Return True if the branch value is safe to echo in URLs."""
    if not branch or len(branch) > 100:
        return False
    return bool(_BRANCH_PARAM_PATTERN.fullmatch(branch))


def sanitize_redirect_path(target: Optional[str], default: str = "/") -> str:
    """Ensure redirect targets stay within the current application."""
    if not target:
        return default

    target = target.strip()
    if not target:
        return default

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default

    path = parsed.path or "/"

    if not path.startswith("/"):
        path = "/" + path.lstrip("/")

    decoded_segments = unquote(path).split("/")
    if any(segment == ".." for segment in decoded_segments):
        return default

    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent security issues."""
    sanitized = _DANGEROUS_FILENAME_CHARS.sub("_", filename).strip()
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip(".")
