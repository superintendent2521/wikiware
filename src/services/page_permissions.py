"""Shared editing policy for pages, branches, and revision restores."""

from ..database import db_instance
from .user_service import UserService


def normalize_allowed_users(value: list | str | None) -> list[str]:
    """Accept current lists and legacy comma-separated usernames."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [name.strip() for name in value if isinstance(name, str) and name.strip()]


def can_user_edit_page(user: dict, page: dict | None) -> bool:
    """Apply page protection to an authenticated user, allowing administrators."""
    if not user:
        return False
    if user.get("is_admin", False) or page is None:
        return True

    permission = (page.get("edit_permission") or "everybody").strip()
    if permission == "everybody":
        return True
    if permission == "10_edits":
        return user.get("total_edits", 0) >= 10
    if permission == "50_edits":
        return user.get("total_edits", 0) >= 50
    if permission == "select_users":
        allowed_users = normalize_allowed_users(page.get("allowed_users"))
        return user.get("username") in allowed_users
    return False


async def is_user_page_title(title: str) -> bool:
    """Recognize personal pages, including legacy mixed-case page titles."""
    if not title or not db_instance.is_connected:
        return False
    if await UserService.get_user_by_username(title) is not None:
        return True
    return any(
        row.get("username", "").casefold() == title.casefold()
        for row in await UserService.list_users()
    )


async def get_page_edit_denial(
    user: dict, title: str, page: dict | None, *, editing_enabled: bool
) -> str | None:
    """Return a denial reason shared by every page-content write entry point."""
    if not user:
        return "Authentication required."
    if not editing_enabled and not user.get("is_admin", False):
        return "Page editing is currently disabled by an administrator."
    # Personal pages remain owner-only, including for administrators.
    if (
        user.get("username", "").casefold() != title.casefold()
        and await is_user_page_title(title)
    ):
        return "You can only edit your own user page."
    if not can_user_edit_page(user, page):
        return "You do not have permission to edit this page."
    return None
