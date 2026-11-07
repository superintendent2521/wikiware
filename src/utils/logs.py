"""
Log utility functions for WikiWare.
Provides core functionality for retrieving and formatting system logs.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from loguru import logger
from ..database import db_instance
from .sql import execute, fetch_all, fetch_one

MAX_PAGE_LIMIT = 50
PAGE_CREATE_FILTER = {"action": "page_create"}
_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


def _normalize_pagination(page: int, limit: int, bypass: bool) -> tuple[int, int]:
    """Return a safe page and limit for log queries."""
    normalized_page = max(1, page)
    normalized_limit = max(1, limit)
    if not bypass:
        normalized_limit = min(MAX_PAGE_LIMIT, normalized_limit)
    return normalized_page, normalized_limit


def _empty_logs_response(
    page: int, limit: int, bypass: bool, pages: int = 0
) -> Dict[str, Any]:
    """Return a consistent empty log response."""
    return {
        "items": [],
        "total": 0,
        "page": 1 if bypass else page,
        "pages": pages,
        "limit": 0 if bypass else limit,
    }


def _wants_action(action_type: Optional[str], expected: str) -> bool:
    """Return whether a request includes the given log action type."""
    return action_type is None or action_type == expected


def _history_log(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map a history document to the public log shape."""
    log_author = item.get("edited_by") or item.get("author", "Anonymous")
    return {
        "type": "edit",
        "title": item.get("title", ""),
        "author": log_author,
        "branch": item.get("branch", "main"),
        "timestamp": item.get("updated_at") or datetime.now(timezone.utc),
        "action": "page_edit",
        "details": {
            "edited_by": log_author,
            "previous_author": item.get("author"),
            "content_length": len(item.get("content") or ""),
        },
    }


def _branch_log(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map a branch document to the public log shape."""
    return {
        "type": "branch_create",
        "title": item.get("page_title", ""),
        "author": "System",
        "branch": item.get("branch_name", "main"),
        "timestamp": item.get("created_at") or datetime.now(timezone.utc),
        "action": "branch_create",
        "details": {"source_branch": item.get("created_from", "main")},
    }


def _page_create_log(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map a system page-creation document to the public log shape."""
    metadata = item.get("metadata") or {}
    author = metadata.get("author") or item.get("username", "Unknown")
    branch = metadata.get("branch", "main")
    return {
        "type": "page_create",
        "title": (
            metadata.get("title")
            or metadata.get("page_title")
            or item.get("message", "")
        ),
        "author": author,
        "branch": branch,
        "timestamp": item.get("timestamp") or datetime.now(timezone.utc),
        "action": "page_create",
        "details": {
            "created_by": metadata.get("author") or item.get("username"),
            "branch": branch,
        },
    }


def _log_sort_key(item: Dict[str, Any]) -> datetime:
    """Return a stable sortable timestamp for mixed or partial log data."""
    timestamp = item.get("timestamp")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp
    return _MIN_TIMESTAMP


async def get_paginated_logs(
    page: int = 1,
    limit: int = 50,
    bypass: bool = False,
    action_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get paginated system logs with optional filtering by action type.

    Args:
        page: Page number (1-indexed)
        limit: Number of items per page (max 50)
        action_type: Filter by action type ("edit", "branch_create", "page_create", or None for all)

    Returns:
        Dictionary containing:
        - items: List of log entries
        - total: Total number of items
        - page: Current page number
        - pages: Total number of pages
        - limit: Items per page
    """
    try:
        page, sanitized_limit = _normalize_pagination(page, limit, bypass)

        if not db_instance.is_connected:
            logger.warning("Database not connected - returning empty logs")
            return _empty_logs_response(page, sanitized_limit, bypass)
        if bypass:
            logger.warning("Bypass flag enabled - pagination limits are ignored")

        history_count = 0
        if _wants_action(action_type, "edit"):
            row = await fetch_one("SELECT COUNT(*) AS count FROM revisions")
            history_count = int(row["count"]) if row else 0

        branch_count = 0
        if _wants_action(action_type, "branch_create"):
            row = await fetch_one("SELECT COUNT(*) AS count FROM page_branches")
            branch_count = int(row["count"]) if row else 0

        create_count = 0
        if _wants_action(action_type, "page_create"):
            row = await fetch_one(
                "SELECT COUNT(*) AS count FROM system_logs WHERE action = :action",
                {"action": "page_create"},
            )
            create_count = int(row["count"]) if row else 0

        total_items = history_count + branch_count + create_count

        if total_items == 0:
            return _empty_logs_response(page, sanitized_limit, bypass, pages=1)

        if bypass:
            effective_limit = total_items
            total_pages = 1
            offset = 0
            current_page = 1
        else:
            effective_limit = sanitized_limit
            total_pages = max(1, (total_items + effective_limit - 1) // effective_limit)
            if page > total_pages:
                return {
                    "items": [],
                    "total": total_items,
                    "page": page,
                    "pages": total_pages,
                    "limit": effective_limit,
                }
            offset = (page - 1) * effective_limit
            current_page = page

        items = []
        fetch_limit = total_items if bypass else offset + effective_limit
        parameters = {"fetch_limit": fetch_limit}

        if history_count:
            history_items = await fetch_all(
                "SELECT p.title, b.name AS branch, r.content, r.author_name AS author, "
                "r.edited_by, r.created_at AS updated_at FROM revisions r "
                "JOIN page_branches b ON b.id = r.page_branch_id "
                "JOIN pages p ON p.id = b.page_id ORDER BY r.created_at DESC "
                "LIMIT :fetch_limit",
                parameters,
            )
            items.extend(_history_log(item) for item in history_items)

        if branch_count:
            branch_items = await fetch_all(
                "SELECT p.title AS page_title, b.name AS branch_name, b.created_from, "
                "b.created_at FROM page_branches b "
                "JOIN pages p ON p.id = b.page_id ORDER BY b.created_at DESC "
                "LIMIT :fetch_limit",
                parameters,
            )
            items.extend(_branch_log(item) for item in branch_items)

        if create_count:
            create_items = await fetch_all(
                "SELECT username, action, message, metadata, timestamp FROM system_logs "
                "WHERE action = :action ORDER BY timestamp DESC LIMIT :fetch_limit",
                {"action": "page_create", "fetch_limit": fetch_limit},
            )
            items.extend(_page_create_log(item) for item in create_items)

        items.sort(key=_log_sort_key, reverse=True)

        if not bypass:
            start_index = offset
            end_index = offset + effective_limit
            items = items[start_index:end_index]

        response_limit = len(items)

        return {
            "items": items,
            "total": total_items,
            "page": current_page,
            "pages": total_pages,
            "limit": response_limit,
        }

    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        raise


async def log_action(
    username: str,
    action: str,
    message: str,
    category: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Persist an admin/system action log entry for auditing.

    Returns True when the entry is stored, False otherwise.
    """
    payload = {
        "username": username,
        "action": action,
        "message": message,
        "category": category,
        "metadata": metadata or {},
        "timestamp": datetime.now(timezone.utc),
    }

    logger.info(
        "Audit log recorded | user={} action={} category={} message={}",
        username,
        action,
        category,
        message,
    )

    if not db_instance.is_connected:
        logger.warning("Database not connected - skipping audit log persistence")
        return False

    try:
        await execute(
            "INSERT INTO system_logs (username, action, message, category, metadata, timestamp) "
            "VALUES (:username, :action, :message, :category, CAST(:metadata AS JSONB), :timestamp)",
            {
                **payload,
                "metadata": json.dumps(payload["metadata"]),
            },
        )
        return True
    except Exception as exc:  # IGNORE W0718
        logger.error("Failed to persist audit log '{}': {}", action, exc)
        return False
