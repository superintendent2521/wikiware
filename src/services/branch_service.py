"""
Branch service layer for WikiWare.
Contains business logic for branch operations.
"""

from typing import List
from datetime import datetime, timezone
from loguru import logger
from ..database import (
    get_pages_collection,
    get_history_collection,
    get_branches_collection,
    db_instance,
)


class BranchService:
    """Service class for branch-related operations."""

    @staticmethod
    async def get_available_branches() -> List[str]:
        """
        Get all available branches across the wiki.

        Returns:
            List of branch names
        """
        try:
            if not db_instance.is_connected:
                logger.warning("Database not connected - cannot get available branches")
                return ["main"]

            branches_collection = get_branches_collection()
            if branches_collection is None:
                logger.warning("Branches collection not available")
                return ["main"]

            branch_names = await branches_collection.distinct("branch_name")
            branches = set(branch_names or [])
            branches.add("main")
            return list(branches)
        except Exception as e:
            logger.error(f"Error getting available branches: {str(e)}")
            return ["main"]

    @staticmethod
    async def get_branches_for_page(title: str) -> List[str]:
        """
        Get all branches for a specific page.

        Args:
            title: Page title

        Returns:
            List of branch names for the page
        """
        try:
            if not db_instance.is_connected:
                logger.warning(
                    f"Database not connected - cannot get branches for page: {title}"
                )
                return ["main"]

            branches_collection = get_branches_collection()
            pages_collection = get_pages_collection()

            branches = {"main"}

            if branches_collection is not None:
                branch_docs = await branches_collection.distinct(
                    "branch_name", {"page_title": title}
                )
                branches.update(branch_docs or [])

            if pages_collection is not None:
                page_branches = await pages_collection.distinct("branch", {"title": title})
                branches.update([b for b in page_branches or [] if b])

            return list(branches)
        except Exception as e:
            logger.error(f"Error getting branches for page {title}: {str(e)}")
            return ["main"]

    @staticmethod
    async def create_branch(
        title: str, branch_name: str, source_branch: str = "main"
    ) -> bool:
        """
        Create a new branch for a page.

        Args:
            title: Page title
            branch_name: New branch name
            source_branch: Source branch to copy from

        Returns:
            True if successful, False otherwise
        """
        try:
            if not db_instance.is_connected:
                logger.error(
                    f"Database not connected - cannot create branch: {branch_name} for page: {title}"
                )
                return False

            pages_collection = get_pages_collection()
            branches_collection = get_branches_collection()
            history_collection = get_history_collection()

            if pages_collection is None or branches_collection is None:
                logger.error("Required collections not available")
                return False

            # Check if branch already exists
            existing_branch = await branches_collection.find_one(
                {"page_title": title, "branch_name": branch_name}
            )
            if existing_branch:
                logger.warning(
                    f"Branch already exists: {branch_name} for page: {title}"
                )
                return False

            # Get source page
            source_page = await pages_collection.find_one(
                {"title": title, "branch": source_branch}
            )
            if not source_page:
                logger.error(
                    f"Source page not found: {title} on branch: {source_branch}"
                )
                return False

            # Create branch entry
            branch_data = {
                "page_title": title,
                "branch_name": branch_name,
                "created_at": datetime.now(timezone.utc),
                "created_from": source_branch,
            }
            await branches_collection.insert_one(branch_data)

            # Copy page to new branch
            new_page = source_page.copy()
            # Remove MongoDB _id field
            new_page.pop("_id", None)
            new_page["branch"] = branch_name
            new_page["created_at"] = datetime.now(timezone.utc)
            new_page["updated_at"] = datetime.now(timezone.utc)
            await pages_collection.insert_one(new_page)

            # Copy history to new branch
            if history_collection is not None:
                source_history = await history_collection.find(
                    {"title": title, "branch": source_branch}
                ).to_list(100)
                for history_item in source_history:
                    new_history_item = history_item.copy()
                    new_history_item.pop("_id", None)
                    new_history_item["branch"] = branch_name
                    await history_collection.insert_one(new_history_item)

            logger.info(
                f"Branch created: {branch_name} for page: {title} from branch: {source_branch}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Error creating branch {branch_name} for page {title}: {str(e)}"
            )
            return False
