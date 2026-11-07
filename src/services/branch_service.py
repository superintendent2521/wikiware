"""Branch operations backed by PostgreSQL foreign keys."""

from datetime import datetime, timezone
from typing import Any, List

from loguru import logger
from sqlalchemy import insert, literal, select
from sqlalchemy.exc import IntegrityError

from ..database import db_instance
from ..schema import branch_editors, page_branches, pages, revisions
from .page_service import _authorize_edit
from .page_errors import PageServiceError, PageUnavailableError, PageConflictError


def _engine() -> Any:
    return getattr(db_instance, "engine", None)


def _ready() -> bool:
    return bool(getattr(db_instance, "is_connected", False) and _engine())


def _name_column() -> Any:
    return page_branches.c.name


def _branch_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key in page_branches.c}


class BranchService:
    """Service class for page branches."""

    @staticmethod
    async def get_available_branches() -> List[str]:
        if not _ready():
            return ["main"]
        try:
            async with _engine().connect() as conn:
                result = await conn.execute(select(_name_column()).distinct().order_by(_name_column()))
                return list(dict.fromkeys(["main"] + [row[0] for row in result]))
        except Exception as exc:
            logger.error(f"Error getting available branches: {exc}")
            return ["main"]

    @staticmethod
    async def get_branches_for_page(title: str) -> List[str]:
        if not _ready():
            return ["main"]
        try:
            statement = (
                select(_name_column())
                .join(pages, page_branches.c.page_id == pages.c.id)
                .where(pages.c.title == title)
                .order_by(_name_column())
            )
            async with _engine().connect() as conn:
                result = await conn.execute(statement)
                return list(dict.fromkeys(["main"] + [row[0] for row in result]))
        except Exception as exc:
            logger.error(f"Error getting branches for page {title}: {exc}")
            return ["main"]

    @staticmethod
    async def create_branch(
        title: str, branch_name: str, source_branch: str = "main", *, actor: dict
    ) -> bool:
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                source_query = (
                    select(page_branches)
                    .join(pages, page_branches.c.page_id == pages.c.id)
                    .where(pages.c.title == title, _name_column() == source_branch)
                    .with_for_update(of=page_branches)
                )
                source = (await conn.execute(source_query)).mappings().first()
                if source is None:
                    logger.error(f"Source page not found: {title} on branch: {source_branch}")
                    return False
                await _authorize_edit(conn, title, source, actor)

                exists = await conn.execute(
                    select(page_branches.c.id).where(
                        page_branches.c.page_id == source["page_id"],
                        _name_column() == branch_name,
                    )
                )
                if exists.first() is not None:
                    return False

                now = datetime.now(timezone.utc)
                values = _branch_values(
                    {
                        "page_id": source["page_id"],
                        "name": branch_name,
                        "branch_name": branch_name,
                        "content": source["content"],
                        "author": source.get("author", source.get("author_name", "Anonymous")),
                        "author_name": source.get("author", source.get("author_name", "Anonymous")),
                        "edit_summary": source.get("edit_summary", ""),
                        "created_at": now,
                        "updated_at": now,
                        "edit_permission": source.get("edit_permission", "everybody"),
                        "allowed_users": source.get("allowed_users", []),
                        "created_from": source_branch,
                        "source_branch": source_branch,
                    }
                )
                branch_id = (await conn.execute(insert(page_branches).values(**values).returning(page_branches.c.id))).scalar_one()

                await conn.execute(
                    insert(branch_editors).from_select(
                        ["page_branch_id", "user_id"],
                        select(literal(branch_id), branch_editors.c.user_id).where(
                            branch_editors.c.page_branch_id == source["id"]
                        ),
                    )
                )

                # INSERT ... SELECT copies every revision, with no arbitrary 100-row ceiling.
                revision_keys = [
                    key for key in ("revision_number", "content", "author", "author_name", "edited_by", "updated_at", "edit_summary", "created_at")
                    if key in revisions.c
                ]
                if "page_branch_id" in revisions.c:
                    revision_values = {"page_branch_id": literal(branch_id)}
                    revision_values.update({key: getattr(revisions.c, key) for key in revision_keys})
                    await conn.execute(
                        insert(revisions).from_select(
                            list(revision_values),
                            select(*revision_values.values())
                            .where(revisions.c.page_branch_id == source["id"]),
                        )
                    )
            logger.info(f"Branch created: {branch_name} for page: {title} from branch: {source_branch}")
            return True
        except PageServiceError:
            raise
        except IntegrityError as exc:
            raise PageConflictError("That branch already exists.") from exc
        except Exception as exc:
            logger.error(f"Error creating branch {branch_name} for page {title}: {exc}")
            raise PageUnavailableError() from exc
