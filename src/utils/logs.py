"""
Log utility functions for WikiWare.
Provides core functionality for retrieving and formatting system logs.
"""

from typing import Dict, Any, Optional
from ..database import get_history_collection, get_branches_collection, db_instance
from loguru import logger

class LogUtils:
    """Utility class for handling system logs."""
    
    @staticmethod
    async def get_paginated_logs(
        page: int = 1, 
        limit: int = 50, 
        action_type: Optional[str] = None
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
            if not db_instance.is_connected:
                logger.warning("Database not connected - returning empty logs")
                return {
                    "items": [],
                    "total": 0,
                    "page": page,
                    "pages": 0,
                    "limit": limit
                }

            # Validate parameters
            if limit > 50:
                limit = 50
            if page < 1:
                page = 1
            
            history_collection = get_history_collection()
            branches_collection = get_branches_collection()
            
            if history_collection is None or branches_collection is None:
                logger.error("Required collections not available")
                return {
                    "items": [],
                    "total": 0,
                    "page": page,
                    "pages": 0,
                    "limit": limit
                }

            # Get total count for pagination
            total_items = 0
            if action_type == "edit" or action_type is None:
                total_items += await history_collection.count_documents({})
            if action_type == "branch_create" or action_type is None:
                total_items += await branches_collection.count_documents({})

            # Calculate total pages
            total_pages = max(1, (total_items + limit - 1) // limit)
            
            # If page is beyond total pages, return empty results
            if page > total_pages:
                return {
                    "items": [],
                    "total": total_items,
                    "page": page,
                    "pages": total_pages,
                    "limit": limit
                }

            # Calculate offset
            offset = (page - 1) * limit
            
            # Get logs based on action_type
            items = []
            
            if action_type == "edit" or action_type is None:
                # Get edit history
                history_cursor = history_collection.find().sort("updated_at", -1)
                history_items = await history_cursor.skip(offset).limit(limit).to_list(limit)
                
                # Format history items
                for item in history_items:
                    items.append({
                        "type": "edit",
                        "title": item["title"],
                        "author": item.get("author", "Anonymous"),
                        "branch": item["branch"],
                        "timestamp": item["updated_at"],
                        "action": "page_edit",
                        "details": {
                            "content_length": len(item.get("content", "")) if "content" in item else 0
                        }
                    })
            
            # If we still need more items for pagination, get branch creation events
            if (action_type == "branch_create" or action_type is None) and len(items) < limit:
                remaining_limit = limit - len(items)
                branches_cursor = branches_collection.find().sort("created_at", -1)
                branches_items = await branches_cursor.skip(max(0, offset - len(items))).limit(remaining_limit).to_list(remaining_limit)
                
                # Format branch creation items
                for item in branches_items:
                    items.append({
                        "type": "branch_create",
                        "title": item["page_title"],
                        "author": "System",  # Branch creation is system-initiated
                        "branch": item["branch_name"],
                        "timestamp": item["created_at"],
                        "action": "branch_create",
                        "details": {
                            "source_branch": item["created_from"]
                        }
                    })
            
            # Sort all items by timestamp (most recent first)
            items.sort(key=lambda x: x["timestamp"], reverse=True)
            
            # Apply final limit if we have more than needed due to combined queries
            if len(items) > limit:
                items = items[:limit]
            
            return {
                "items": items,
                "total": total_items,
                "page": page,
                "pages": total_pages,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error fetching logs: {str(e)}")
            raise e
