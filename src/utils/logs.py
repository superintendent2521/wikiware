"""
Log utility functions for WikiWare.
Provides core functionality for retrieving and formatting system logs.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from loguru import logger
from ..database import TABLE_PREFIX, db_instance


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
        if page < 1:
            page = 1

        sanitized_limit = max(1, limit)
        if not bypass and sanitized_limit > 50:
            sanitized_limit = 50

        if not db_instance.is_connected:
            logger.warning("Database not connected - returning empty logs")
            return {
                "items": [],
                "total": 0,
                "page": 1 if bypass else page,
                "pages": 0,
                "limit": 0 if bypass else sanitized_limit,
            }
        if bypass:
            logger.warning("Bypass flag enabled - pagination limits are ignored")
        selects = _build_log_selects(action_type)
        if not selects:
            logger.warning("No log sources included for requested action type")
            return {
                "items": [],
                "total": 0,
                "page": 1 if bypass else page,
                "pages": 0,
                "limit": 0 if bypass else sanitized_limit,
            }

        union_sql = " UNION ALL ".join(selects)
        count_query = f"SELECT COUNT(*) AS count FROM ({union_sql}) AS combined"
        count_rows = await db_instance.fetch(count_query)
        total_items = int(count_rows[0]["count"]) if count_rows else 0

        if total_items == 0:
            total_pages = 1
            current_page = 1 if bypass else page
            return {
                "items": [],
                "total": 0,
                "page": current_page,
                "pages": total_pages,
                "limit": 0 if bypass else sanitized_limit,
            }

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

        data_params: List[Any] = []
        data_query = f"SELECT * FROM ({union_sql}) AS combined ORDER BY timestamp DESC"
        if not bypass:
            data_query += (
                f" OFFSET ${len(data_params) + 1} LIMIT ${len(data_params) + 2}"
            )
            data_params.extend([offset, effective_limit])

        rows = await db_instance.fetch(data_query, *data_params)
        items = _rows_to_items(rows)

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
        raise e


def _build_log_selects(action_type: Optional[str]) -> List[str]:
    history_table = f"{TABLE_PREFIX}history"
    branches_table = f"{TABLE_PREFIX}branches"
    system_logs_table = f"{TABLE_PREFIX}system_logs"

    selects: List[str] = []

    if action_type in (None, "edit"):
        selects.append(
            f"""
            SELECT
                'edit' AS type,
                doc #>> '{{title}}' AS title,
                COALESCE(NULLIF(doc #>> '{{edited_by}}', ''), doc #>> '{{author}}', 'Anonymous') AS author,
                COALESCE(doc #>> '{{branch}}', 'main') AS branch,
                (doc #>> '{{updated_at}}')::timestamptz AS timestamp,
                'page_edit' AS action,
                jsonb_build_object(
                    'edited_by', COALESCE(NULLIF(doc #>> '{{edited_by}}', ''), doc #>> '{{author}}', 'Anonymous'),
                    'previous_author', doc #>> '{{author}}',
                    'content_length', COALESCE(length(doc #>> '{{content}}'), 0)
                ) AS details
            FROM {history_table}
            WHERE doc ? 'updated_at'
            """
        )

    if action_type in (None, "branch_create"):
        selects.append(
            f"""
            SELECT
                'branch_create' AS type,
                doc #>> '{{page_title}}' AS title,
                'System' AS author,
                doc #>> '{{branch_name}}' AS branch,
                (doc #>> '{{created_at}}')::timestamptz AS timestamp,
                'branch_create' AS action,
                jsonb_build_object('source_branch', doc #>> '{{created_from}}') AS details
            FROM {branches_table}
            WHERE doc ? 'created_at'
            """
        )

    if action_type in (None, "page_create"):
        selects.append(
            f"""
            SELECT
                'page_create' AS type,
                COALESCE(
                    NULLIF(doc #>> '{{metadata,title}}', ''),
                    NULLIF(doc #>> '{{metadata,page_title}}', ''),
                    doc #>> '{{message}}',
                    ''
                ) AS title,
                COALESCE(doc #>> '{{metadata,author}}', doc #>> '{{username}}', 'Unknown') AS author,
                COALESCE(doc #>> '{{metadata,branch}}', 'main') AS branch,
                (doc #>> '{{timestamp}}')::timestamptz AS timestamp,
                'page_create' AS action,
                jsonb_build_object(
                    'created_by', COALESCE(doc #>> '{{metadata,author}}', doc #>> '{{username}}'),
                    'branch', COALESCE(doc #>> '{{metadata,branch}}', 'main')
                ) AS details
            FROM {system_logs_table}
            WHERE (doc #>> '{{action}}') = 'page_create' AND doc ? 'timestamp'
            """
        )

    return selects


def _rows_to_items(rows: List[Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "type": row.get("type"),
                "title": row.get("title"),
                "author": row.get("author"),
                "branch": row.get("branch"),
                "timestamp": row.get("timestamp"),
                "action": row.get("action"),
                "details": row.get("details") or {},
            }
        )
    return items


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

    collection = db_instance.get_collection("system_logs")
    if collection is None:
        logger.warning("system_logs collection unavailable - skipping audit log")
        return False

    try:
        await collection.insert_one(payload)
        return True
    except Exception as exc:  # IGNORE W0718
        logger.error("Failed to persist audit log '{}': {}", action, exc)
        return False
