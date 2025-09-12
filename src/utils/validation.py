"""
Validation utilities for WikiWare.
Contains common validation functions used across the application.
"""

def is_valid_title(title: str) -> bool:
    """
    Validate title to prevent path traversal or other issues.

    Args:
        title: The title to validate

    Returns:
        bool: True if title is valid, False otherwise
    """
    return title and ".." not in title and not title.startswith("/")


def is_valid_branch_name(branch_name: str) -> bool:
    """
    Validate branch name to ensure it's safe and follows naming conventions.

    Args:
        branch_name: The branch name to validate

    Returns:
        bool: True if branch name is valid, False otherwise
    """
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


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent security issues.

    Args:
        filename: The filename to sanitize

    Returns:
        str: Sanitized filename
    """
    # Remove path separators and other dangerous characters
    dangerous_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    sanitized = filename

    for char in dangerous_chars:
        sanitized = sanitized.replace(char, '_')

    return sanitized
