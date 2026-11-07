"""API routes for history metadata used by client-side widgets."""

from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from loguru import logger

from ...database import db_instance
from ...utils.validation import is_safe_branch_parameter, is_valid_title
from ..web.history import (
    _build_version_entries,
    _fetch_versions_for_history,
)

router = APIRouter()


def _format_version_entry(
    title: str, branch: str, entry: Dict[str, Any]
) -> Dict[str, Any]:
    """Prepare a simplified payload for history dropdown consumers."""
    encoded_title = quote(title, safe="")
    encoded_branch = quote(branch, safe="")
    branch_query = f"?branch={encoded_branch}" if branch != "main" else ""
    view_url = f"/history/{encoded_title}/{entry['index']}{branch_query}"
    if entry.get("revision_id") is not None:
        separator = "&" if branch_query else "?"
        view_url += f"{separator}revision_id={entry['revision_id']}"
    history_url = f"/history/{encoded_title}{branch_query}"
    compare_url = (
        f"/history/{encoded_title}/compare{branch_query}"
        if not branch_query
        else f"/history/{encoded_title}/compare?branch={encoded_branch}"
    )
    label = (
        f"Version {entry['display_number']} — {entry['author']}"
        f" ({entry['updated_at_display']})"
    )
    return {
        "index": entry["index"],
        "revision_id": entry.get("revision_id"),
        "display_number": entry["display_number"],
        "author": entry["author"],
        "updated_at": entry["updated_at_display"],
        "is_current": entry["is_current"],
        "view_url": view_url,
        "history_url": history_url,
        "compare_url": compare_url,
        "label": label,
    }


@router.get("/history/{title}")
async def get_history_versions(title: str, branch: str = "main", limit: int = 10):
    """Return recent history entries for the requested page."""
    normalized_branch = (branch or "main").strip() or "main"
    if normalized_branch != "main" and not is_safe_branch_parameter(normalized_branch):
        raise HTTPException(status_code=400, detail="Invalid branch parameter")

    if not is_valid_title(title):
        raise HTTPException(status_code=400, detail="Invalid page title")

    if not db_instance.is_connected:
        raise HTTPException(status_code=503, detail="Database not available")

    history_limit = max(1, min(50, int(limit or 10)))

    try:
        versions_raw = await _fetch_versions_for_history(
            title,
            normalized_branch,
            limit=history_limit,
        )
        entries = _build_version_entries(versions_raw)
    except Exception as exc:
        logger.error(
            f"Database error while fetching history for {title} "
            f"(branch: {normalized_branch}): {exc}"
        )
        raise HTTPException(status_code=500, detail="Failed to fetch history data")

    formatted_versions: List[Dict[str, Any]] = [
        _format_version_entry(title, normalized_branch, entry) for entry in entries
    ]
    return {
        "title": title,
        "branch": normalized_branch,
        "versions": formatted_versions,
    }
