"""
Log utility functions for WikiWare.
Provides core functionality for retrieving and formatting system logs.
"""

from typing import Dict, Any, Optional
from loguru import logger
from ..database import get_history_collection, get_branches_collection, db_instance


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
        action_type: Filter by action type ("edit", "branch_create", or None for all)

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

        history_collection = get_history_collection()
        branches_collection = get_branches_collection()

        if history_collection is None or branches_collection is None:
            logger.error("Required collections not available")
            return {
                "items": [],
                "total": 0,
                "page": 1 if bypass else page,
                "pages": 0,
                "limit": 0 if bypass else sanitized_limit,
            }

        history_count = 0
        if action_type == "edit" or action_type is None:
            history_count = await history_collection.count_documents({})

        branch_count = 0
        if action_type == "branch_create" or action_type is None:
            branch_count = await branches_collection.count_documents({})

        total_items = history_count + branch_count

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
            total_pages = max(
                1, (total_items + effective_limit - 1) // effective_limit
            )
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

        if action_type == "edit" or action_type is None:
            history_cursor = history_collection.find().sort("updated_at", -1)
            history_skip = 0 if bypass else offset
            history_fetch_limit = history_count if bypass else effective_limit
            history_items = []
            if history_fetch_limit > 0:
                history_items = (
                    await history_cursor.skip(history_skip)
                    .limit(history_fetch_limit)
                    .to_list(history_fetch_limit)
                )

            for item in history_items:
                log_author = item.get("edited_by") or item.get("author", "Anonymous")
                items.append(
                    {
                        "type": "edit",
                        "title": item["title"],
                        "author": log_author,
                        "branch": item["branch"],
                        "timestamp": item["updated_at"],
                        "action": "page_edit",
                        "details": {
                            "edited_by": log_author,
                            "previous_author": item.get("author"),
                            "content_length": (
                                len(item.get("content", ""))
                                if "content" in item
                                else 0
                            )
                        },
                    }
                )

        if action_type == "branch_create" or action_type is None:
            needs_branches = bypass or len(items) < effective_limit
            if needs_branches:
                branches_cursor = branches_collection.find().sort("created_at", -1)
                if bypass:
                    remaining_limit = max(0, effective_limit - len(items))
                    branches_offset = 0
                else:
                    remaining_limit = effective_limit - len(items)
                    branches_offset = max(0, offset - len(items))

                if remaining_limit > 0:
                    branches_items = (
                        await branches_cursor.skip(branches_offset)
                        .limit(remaining_limit)
                        .to_list(remaining_limit)
                    )

                    for item in branches_items:
                        items.append(
                            {
                                "type": "branch_create",
                                "title": item["page_title"],
                                "author": "System",
                                "branch": item["branch_name"],
                                "timestamp": item["created_at"],
                                "action": "branch_create",
                                "details": {"source_branch": item["created_from"]},
                            }
                        )

        items.sort(key=lambda x: x["timestamp"], reverse=True)

        if not bypass and len(items) > effective_limit:
            items = items[:effective_limit]

        response_limit = len(items) if bypass else effective_limit

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
