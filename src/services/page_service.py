"""
Page service layer for WikiWare.
Contains business logic for page operations.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from loguru import logger
from ..database import (
    get_pages_collection,
    get_history_collection,
    get_users_collection,
    get_branches_collection,
    db_instance,
)


class PageService:
    """Service class for page-related operations."""

    @staticmethod
    def _normalize_summary(edit_summary: Optional[str]) -> str:
        summary = (edit_summary or "").strip()
        if len(summary) > 250:
            return summary[:250]
        return summary

    @staticmethod
    async def get_page(title: str, branch: str = "main") -> Optional[Dict[str, Any]]:
        """
        Get a page by title and branch.

        Args:
            title: Page title
            branch: Branch name

        Returns:
            Page document or None if not found
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot get page: {title} on branch: {branch}"
                )
                return None

            pages_collection = get_pages_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return None

            page = await pages_collection.find_one({"title": title, "branch": branch})
            return page
        except Exception as e:
            logger.error(f"Error getting page {title} on branch {branch}: {str(e)}")
            return None

    @staticmethod
    async def create_page(
        title: str,
        content: str,
        author: str = "Anonymous",
        branch: str = "main",
        edit_summary: Optional[str] = None,
    ) -> bool:
        """
        Create a new page.

        Args:
            title: Page title
            content: Page content
            author: Author name
            branch: Branch name
            edit_summary: Optional summary describing the change

        Returns:
            True if successful, False otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.error(
                    f"Database not connected - cannot create page: {title} on branch: {branch}"
                )
                return False

            pages_collection = get_pages_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return False

            summary = PageService._normalize_summary(edit_summary)

            page_data = {
                "title": title,
                "content": content,
                "author": author,
                "branch": branch,
                "edit_summary": summary,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }

            await pages_collection.insert_one(page_data)
            logger.info(f"Page created: {title} on branch: {branch} by {author}")
            return True
        except Exception as e:
            logger.error(f"Error creating page {title} on branch {branch}: {str(e)}")
            return False

    @staticmethod
    async def update_page(
        title: str,
        content: str,
        author: str = "Anonymous",
        branch: str = "main",
        edit_summary: Optional[str] = None,
    ) -> bool:
        """
        Update an existing page.

        Args:
            title: Page title
            content: New content
            author: Author name
            branch: Branch name
            edit_summary: Optional summary describing the change

        Returns:
            True if successful, False otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.error(
                    f"Database not connected - cannot update page: {title} on branch: {branch}"
                )
                return False

            pages_collection = get_pages_collection()
            history_collection = get_history_collection()
            users_collection = get_users_collection()

            if pages_collection is None:
                logger.error("Pages collection not available")
                return False

            summary = PageService._normalize_summary(edit_summary)

            existing_page = await pages_collection.find_one(
                {"title": title, "branch": branch}
            )

            if existing_page:
                if history_collection is not None:
                    history_item = {
                        "title": title,
                        "content": existing_page["content"],
                        "author": existing_page.get("author", "Anonymous"),
                        "branch": branch,
                        "updated_at": existing_page["updated_at"],
                        "edit_summary": existing_page.get("edit_summary", ""),
                    }
                    await history_collection.insert_one(history_item)

                await pages_collection.update_one(
                    {"title": title, "branch": branch},
                    {
                        "$set": {
                            "content": content,
                            "author": author,
                            "edit_summary": summary,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )

                if users_collection is not None and author != "Anonymous":
                    await users_collection.update_one(
                        {"username": author}, {"$inc": {"total_edits": 1}}
                    )
                    await users_collection.update_one(
                        {"username": author}, {"$inc": {f"page_edits.{title}": 1}}
                    )

                logger.info(f"Page updated: {title} on branch: {branch} by {author}")
                return True
            else:
                created = await PageService.create_page(
                    title, content, author, branch, edit_summary=summary
                )
                if created and author != "Anonymous":
                    if users_collection is not None:
                        await users_collection.update_one(
                            {"username": author},
                            {"$inc": {"total_edits": 1, f"page_edits.{title}": 1}},
                        )
                return created
        except Exception as e:
            logger.error(f"Error updating page {title} on branch {branch}: {str(e)}")
            return False

    @staticmethod
    async def get_pages_by_branch(
        branch: str = "main", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all pages for a specific branch.

        Args:
            branch: Branch name
            limit: Maximum number of pages to return

        Returns:
            List of page documents
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot get pages for branch: {branch}"
                )
                return []

            pages_collection = get_pages_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return []

            pages = (
                await pages_collection.find({"branch": branch})
                .sort("updated_at", -1)
                .to_list(limit)
            )
            return pages
        except Exception as e:
            logger.error(f"Error getting pages for branch {branch}: {str(e)}")
            return []

    @staticmethod
    async def search_pages(
        query: str, branch: str = "main", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search pages by title or content.

        Args:
            query: Search query
            branch: Branch name
            limit: Maximum number of results

        Returns:
            List of matching page documents
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot search pages with query: {query}"
                )
                return []

            pages_collection = get_pages_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return []

            pages = await pages_collection.find(
                {
                    "$and": [
                        {"branch": branch},
                        {
                            "$or": [
                                {"title": {"$regex": query, "$options": "i"}},
                                {"content": {"$regex": query, "$options": "i"}},
                            ]
                        },
                    ]
                }
            ).to_list(limit)

            logger.info(
                f"Search performed: '{query}' on branch '{branch}' - found {len(pages)} results"
            )
            return pages
        except Exception as e:
            logger.error(
                f"Error searching pages with query '{query}' on branch '{branch}': {str(e)}"
            )
            return []

    @staticmethod
    async def delete_page(title: str) -> bool:
        """
        Delete all branches of a page (effectively deleting the page entirely).

        Args:
            title: Page title

        Returns:
            True if successful, False otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.error(f"Database not connected - cannot delete page: {title}")
                return False

            pages_collection = get_pages_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return False

            result = await pages_collection.delete_many({"title": title})
            if result.deleted_count > 0:
                logger.info(
                    f"Page deleted (all branches): {title} ({result.deleted_count} branches removed)"
                )
                return True
            else:
                logger.warning(f"Page not found for deletion: {title}")
                return False
        except Exception as e:
            logger.error(f"Error deleting page {title}: {str(e)}")
            return False

    @staticmethod
    async def delete_branch(title: str, branch: str) -> bool:
        """
        Delete a specific branch from a specific page across both collections.
        Supports either (title, branch) or (page_title, branch_name) schemas.
        """
        logger.info(f"Attempting to delete branch {branch} from page {title}")
        try:
            if not db_instance.is_connected:
                logger.error(
                    f"Database not connected - cannot delete branch {branch} from page {title}"
                )
                return False

            pages_collection = get_pages_collection()
            branches_collection = get_branches_collection()
            if pages_collection is None:
                logger.error("Pages collection not available")
                return False
            if branches_collection is None:
                logger.error("Branches collection not available")
                return False

            # Build a schema-tolerant filter for pages
            pages_filter = {
                "$and": [
                    {"$or": [{"title": title}, {"page_title": title}]},
                    {"$or": [{"branch": branch}, {"branch_name": branch}]},
                ]
            }

            # (Optional) preview how many matches exist with each variant for better logs
            count_title_branch = await pages_collection.count_documents(
                {"title": title, "branch": branch}
            )
            count_title_branchname = await pages_collection.count_documents(
                {"title": title, "branch_name": branch}
            )
            count_pagetitle_branch = await pages_collection.count_documents(
                {"page_title": title, "branch": branch}
            )
            count_pagetitle_branchname = await pages_collection.count_documents(
                {"page_title": title, "branch_name": branch}
            )
            logger.info(
                "Pages match counts — "
                f"(title,branch)={count_title_branch}, "
                f"(title,branch_name)={count_title_branchname}, "
                f"(page_title,branch)={count_pagetitle_branch}, "
                f"(page_title,branch_name)={count_pagetitle_branchname}"
            )

            # Delete ALL page docs for this (title, branch)
            page_del_result = await pages_collection.delete_many(pages_filter)

            if page_del_result.deleted_count == 0:
                logger.warning(
                    f"No page docs deleted for ({title}, {branch}). "
                    "Check pages schema and indexes."
                )
                # Don't return yet; still attempt branch record delete

            # Delete from branches collection (per your schema example)
            branch_del_result = await branches_collection.delete_one(
                {"page_title": title, "branch_name": branch}
            )
            if branch_del_result.deleted_count == 0:
                logger.warning(
                    f"Branch record not found in branches collection: {branch} for page {title}"
                )

            if page_del_result.deleted_count > 0:
                logger.info(
                    f"Deleted {page_del_result.deleted_count} page doc(s) and "
                    f"{branch_del_result.deleted_count} branch doc(s) for ({title}, {branch})"
                )
                return True

            # If pages weren't deleted but branch was (or wasn't), consider operation incomplete
            return False

        except Exception as e:
            logger.error(f"Error deleting branch {branch} from page {title}: {str(e)}")
            return False
