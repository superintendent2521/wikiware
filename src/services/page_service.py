"""
Page service layer for WikiWare.
Contains business logic for page operations.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from loguru import logger
from pymongo.errors import OperationFailure
from ..database import (
    TABLE_PREFIX,
    get_pages_collection,
    get_history_collection,
    get_users_collection,
    get_branches_collection,
    db_instance,
)
from ..utils.logs import log_action


class PageService:
    """Service class for page-related operations."""

    @staticmethod
    def _normalize_timestamp(page: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure updated_at is a datetime for template usage."""
        ts = page.get("updated_at")
        if isinstance(ts, str):
            try:
                page["updated_at"] = datetime.fromisoformat(ts)
            except ValueError:
                page["updated_at"] = None
        return page

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
            if page and "_id" in page:
                page["_id"] = str(page["_id"])
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
        edit_permission: str = "everybody",
        allowed_users: Optional[List[str]] = None,
        connection: Any = None,
    ) -> bool:
        """
        Create a new page.

        Args:
            title: Page title
            content: Page content
            author: Author name
            branch: Branch name
            edit_summary: Optional summary describing the change
            edit_permission: Edit protection level for the page
            allowed_users: Optional list of usernames allowed to edit when select_users protection is used

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
            normalized_allowed_users = allowed_users or []

            # For talk branches, add signature to content
            if branch == "talk":
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                signed_content = f"{content} ([[User:{author}]] {timestamp})"
            else:
                signed_content = content

            page_data = {
                "title": title,
                "content": signed_content,
                "author": author,
                "branch": branch,
                "edit_summary": summary,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "edit_permission": edit_permission,
                "allowed_users": normalized_allowed_users,
            }

            await pages_collection.insert_one(page_data, connection=connection)
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
        edit_permission: str = "everybody",
        allowed_users: Optional[List[str]] = None,
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
                        "edited_by": author,
                        "branch": branch,
                        "updated_at": existing_page["updated_at"],
                        "edit_summary": existing_page.get("edit_summary", ""),
                    }
                    await history_collection.insert_one(history_item)

                # For talk branch, append with signature instead of replacing
                if branch == "talk":
                    timestamp = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                    signature = f"\n\n{content} ([[User:{author}]] {timestamp})"
                    new_content = existing_page["content"] + signature
                else:
                    new_content = content

                await pages_collection.update_one(
                    {"title": title, "branch": branch},
                    {
                        "$set": {
                            "content": new_content,
                            "author": author,
                            "edit_summary": summary,
                            "edit_permission": edit_permission,
                            "allowed_users": allowed_users or [],
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
                # Check if this is the first branch ever created for this page

                any_existing_page = await pages_collection.find_one({"title": title})
                if not any_existing_page:
                    # Create both main and talk branches for new pages atomically
                    async with db_instance.transaction() as conn:
                        created_main = await PageService.create_page(
                            title,
                            content,
                            author,
                            "main",
                            edit_summary=summary,
                            edit_permission=edit_permission,
                            allowed_users=allowed_users or [],
                            connection=conn,
                        )
                        created_talk = await PageService.create_page(
                            title,
                            "",
                            author,
                            "talk",
                            edit_summary="wikibot: Auto-created talk page",
                            edit_permission=edit_permission,
                            allowed_users=allowed_users or [],
                            connection=conn,
                        )
                    if created_main and created_talk:
                        await log_action(
                            author,
                            "page_create",
                            f"Page '{title}' created on branch 'main'",
                            category="page",
                            metadata={
                                "title": title,
                                "branch": "main",
                                "author": author,
                            },
                        )
                        if author != "Anonymous" and users_collection is not None:
                            await users_collection.update_one(
                                {"username": author},
                                {"$inc": {"total_edits": 2, f"page_edits.{title}": 2}},
                            )
                        return True
                    else:
                        return False
                else:
                    # Page exists on other branches, just create this specific branch
                    created = await PageService.create_page(
                        title,
                        content,
                        author,
                        branch,
                        edit_summary=summary,
                        edit_permission=edit_permission,
                        allowed_users=allowed_users or [],
                    )
                    if (
                        created
                        and author != "Anonymous"
                        and users_collection is not None
                    ):
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
            pages = [PageService._normalize_timestamp(p) for p in pages]
            return pages
        except Exception as e:
            logger.error(f"Error getting pages for branch {branch}: {str(e)}")
            return []

    @staticmethod
    async def search_pages(
        query: str, branch: str = "main", limit: int = 100
    ) -> List[Dict[str, Any]]:
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

            normalized_query = query.strip()
            if not normalized_query:
                logger.info("Search attempted with empty query")
                return []

            table_name = f"{TABLE_PREFIX}pages"
            search_sql = f"""
                WITH search AS (
                    SELECT
                        doc,
                        to_tsvector(
                            'english',
                            coalesce(doc->>'title', '') || ' ' || coalesce(doc->>'content', '')
                        ) AS search_vector,
                        websearch_to_tsquery('english', $1) AS search_query
                    FROM {table_name}
                )
                SELECT
                    doc #>> '{{title}}' AS title,
                    doc #>> '{{content}}' AS content,
                    NULLIF(doc #>> '{{updated_at}}', '')::timestamptz AS updated_at,
                    coalesce(doc #>> '{{branch}}', 'main') AS branch,
                    coalesce(NULLIF(doc #>> '{{author}}', ''), 'Anonymous') AS author,
                    ts_rank(search_vector, search_query) AS rank
                FROM search
                WHERE coalesce(doc #>> '{{branch}}', 'main') = $2
                  AND search_vector @@ search_query
                ORDER BY rank DESC, updated_at DESC
                LIMIT $3
            """

            effective_limit = 100 if limit is None else limit
            rows = await db_instance.fetch(
                search_sql, normalized_query, branch or "main", effective_limit
            )

            pages = []
            for row in rows:
                pages.append(
                    {
                        "title": row["title"],
                        "content": row["content"],
                        "updated_at": row["updated_at"],
                        "branch": row["branch"],
                        "author": row["author"],
                    }
                )

            pages = [PageService._normalize_timestamp(p) for p in pages]
            logger.info(f"Search performed: {query!r} on branch {branch!r} - found {len(pages)} results")
            return pages

        except Exception as e:
            logger.error(
                f"Error searching pages with query {query!r} on branch {branch!r}: {e}"
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

    @staticmethod
    async def rename_page(old_title: str, new_title: str) -> tuple[bool, str | None]:
        """Rename a page across all related collections."""
        try:
            if old_title == new_title:
                logger.info(
                    f"Rename skipped because the old and new titles are identical: {old_title}"
                )
                return True, None

            if not db_instance.is_connected:
                logger.error(
                    f"Database not connected - cannot rename page: {old_title} to {new_title}"
                )
                return False, "offline"

            pages_collection = get_pages_collection()
            history_collection = get_history_collection()
            branches_collection = get_branches_collection()
            users_collection = get_users_collection()

            if pages_collection is None:
                logger.error("Pages collection not available")
                return False, "pages_collection_missing"

            existing_page = await pages_collection.find_one({"title": old_title})
            if existing_page is None:
                logger.warning(
                    f"Cannot rename page '{old_title}' because it was not found"
                )
                return False, "not_found"

            conflict_page = await pages_collection.find_one({"title": new_title})
            if conflict_page is not None:
                logger.warning(
                    f"Cannot rename page '{old_title}' to '{new_title}' because the target title exists"
                )
                return False, "conflict"

            now = datetime.now(timezone.utc)
            page_update_result = await pages_collection.update_many(
                {"title": old_title},
                {"$set": {"title": new_title, "updated_at": now}},
            )

            if page_update_result.matched_count == 0:
                logger.error(
                    f"Rename failed because no page documents matched '{old_title}' despite prior lookup"
                )
                return False, "not_found"

            if history_collection is not None:
                await history_collection.update_many(
                    {"title": old_title},
                    {"$set": {"title": new_title}},
                )

            if branches_collection is not None:
                await branches_collection.update_many(
                    {"page_title": old_title},
                    {"$set": {"page_title": new_title}},
                )

            if users_collection is not None:
                async for user_doc in users_collection.find(
                    {f"page_edits.{old_title}": {"$exists": True}}
                ):
                    page_edits = user_doc.get("page_edits", {})
                    if not isinstance(page_edits, dict):
                        continue
                    edit_count = page_edits.get(old_title)
                    if edit_count is None:
                        continue
                    await users_collection.update_one(
                        {"_id": user_doc["_id"]},
                        {
                            "$set": {f"page_edits.{new_title}": edit_count},
                            "$unset": {f"page_edits.{old_title}": ""},
                        },
                    )

            logger.info(f"Page renamed from {old_title} to {new_title}")
            return True, None
        except Exception as e:
            logger.error(
                f"Error renaming page {old_title} to {new_title}: {str(e)}"
            )
            return False, "error"
