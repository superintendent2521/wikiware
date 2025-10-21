"""
Navigation history helpers for page views.
Provides cookie-backed storage for recent wiki navigation.
"""

import json
from typing import Dict, List, Optional, Tuple

from fastapi import Request

HISTORY_COOKIE_NAME = "wiki_page_history"
HISTORY_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days
HISTORY_MAX_LENGTH = 20
HISTORY_QUERY_PARAM = "nav"
HISTORY_BACK_VALUE = "back"


def build_history_entry(title: str, branch: str, is_home: bool) -> Dict[str, object]:
    """Return a normalized navigation history entry for storage."""
    normalized_branch = branch or "main"
    return {
        "title": title,
        "branch": normalized_branch,
        "is_home": bool(is_home),
    }


def load_history_cookie(request: Request) -> List[Dict[str, object]]:
    """Return previously stored navigation history from the request cookie."""
    raw_history = request.cookies.get(HISTORY_COOKIE_NAME)
    if not raw_history:
        return []
    try:
        loaded_history = json.loads(raw_history)
    except (TypeError, ValueError):
        return []

    history: List[Dict[str, object]] = []
    for entry in loaded_history:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title:
            continue
        branch_value = entry.get("branch") if isinstance(entry.get("branch"), str) else "main"
        history.append(
            {
                "title": title,
                "branch": branch_value or "main",
                "is_home": bool(entry.get("is_home")),
            }
        )
    return history[:HISTORY_MAX_LENGTH]


def apply_history_update(
    history: List[Dict[str, object]],
    current_entry: Dict[str, object],
    is_back_navigation: bool,
) -> List[Dict[str, object]]:
    """Return the navigation history updated with the current page visit."""
    updated_history = list(history)

    if is_back_navigation:
        while updated_history and updated_history[-1] != current_entry:
            updated_history.pop()
        if not updated_history or updated_history[-1] != current_entry:
            updated_history.append(current_entry)
        return updated_history

    updated_history = [entry for entry in updated_history if entry != current_entry]
    updated_history.append(current_entry)
    if len(updated_history) > HISTORY_MAX_LENGTH:
        updated_history = updated_history[-HISTORY_MAX_LENGTH :]
    return updated_history


def resolve_previous_entry(
    history: List[Dict[str, object]], current_entry: Dict[str, object]
) -> Optional[Dict[str, object]]:
    """Return the most recent unique entry prior to the current visit."""
    if len(history) < 2:
        return None

    for index in range(len(history) - 2, -1, -1):
        entry = history[index]
        if (
            entry.get("title") == current_entry.get("title")
            and entry.get("branch") == current_entry.get("branch")
        ):
            continue
        return entry
    return None


def build_history_link(request: Request, entry: Dict[str, object]) -> str:
    """Return a URL for the provided navigation entry including the back flag."""
    branch_value = str(entry.get("branch") or "main")
    if entry.get("is_home"):
        target_url = request.url_for("home")
    else:
        target_url = request.url_for("get_page", title=str(entry.get("title", "")))

    query_params = {HISTORY_QUERY_PARAM: HISTORY_BACK_VALUE}
    if branch_value != "main":
        query_params["branch"] = branch_value

    return str(target_url.include_query_params(**query_params))


def serialize_history(history: List[Dict[str, object]]) -> str:
    """Return the serialized history string suitable for a cookie value."""
    return json.dumps(history, separators=(",", ":"))


def prepare_navigation_context(
    request: Request,
    title: str,
    branch: str,
    is_home: bool,
) -> Tuple[List[Dict[str, object]], Optional[Dict[str, str]]]:
    """Return updated history data and previous page context for templates."""
    history = load_history_cookie(request)
    current_entry = build_history_entry(title, branch, is_home)
    is_back_navigation = request.query_params.get(HISTORY_QUERY_PARAM) == HISTORY_BACK_VALUE
    updated_history = apply_history_update(history, current_entry, is_back_navigation)
    previous_entry = resolve_previous_entry(updated_history, current_entry)

    if not previous_entry or previous_entry == current_entry:
        return updated_history, None

    previous_context = {
        "title": str(previous_entry.get("title", "")),
        "url": build_history_link(request, previous_entry),
    }
    return updated_history, previous_context
