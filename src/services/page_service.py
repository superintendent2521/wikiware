"""Relational page and revision operations."""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import case, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from ..database import db_instance
from ..schema import (
    branch_editors,
    page_branches,
    pages,
    revisions,
    user_page_edit_counts,
    users,
)
from ..utils.logs import log_action
from .page_errors import (
    PageServiceError, PageUnavailableError, PageConflictError, PagePermissionError,
    page_version_token,
)
from .page_permissions import can_user_edit_page, normalize_allowed_users
from .settings_service import FeatureFlags
from ..schema import settings


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row._mapping if hasattr(row, "_mapping") else row)


def _page_dict(row: Any, allowed_users: Optional[List[str]] = None) -> Dict[str, Any]:
    value = _row_dict(row)
    value["branch"] = value.pop("branch_name", value.pop("name", value.get("branch", "main")))
    value["author"] = value.pop("author_name", value.get("author", "Anonymous"))
    value["allowed_users"] = allowed_users or []
    if "id" in value:
        value["_id"] = str(value["id"])
    value["version_token"] = page_version_token(value)
    return value


def _engine() -> Any:
    return getattr(db_instance, "engine", None)


def _ready() -> bool:
    return bool(getattr(db_instance, "is_connected", False) and _engine())


def _branch_columns(values: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if key in page_branches.c}


def _branch_name_column() -> Any:
    return page_branches.c.name


def _revision_time_column() -> Any:
    return revisions.c.created_at


def _revision_columns(values: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if key in revisions.c}


async def _next_revision_number(conn: Any, branch_id: Any) -> int:
    result = await conn.execute(
        select(func.coalesce(func.max(revisions.c.revision_number), 0) + 1).where(
            revisions.c.page_branch_id == branch_id
        )
    )
    return int(result.scalar_one())


async def _allowed_users_by_branch(
    conn: Any, branch_ids: List[int]
) -> Dict[int, List[str]]:
    if not branch_ids:
        return {}
    rows = (
        await conn.execute(
            select(branch_editors.c.page_branch_id, users.c.username)
            .join(users, users.c.id == branch_editors.c.user_id)
            .where(branch_editors.c.page_branch_id.in_(branch_ids))
            .order_by(users.c.username)
        )
    ).all()
    result: Dict[int, List[str]] = defaultdict(list)
    for branch_id, username in rows:
        result[branch_id].append(username)
    return result


async def _set_allowed_users(
    conn: Any, branch_id: int, usernames: Optional[List[str]]
) -> None:
    await conn.execute(
        delete(branch_editors).where(branch_editors.c.page_branch_id == branch_id)
    )
    normalized = list(dict.fromkeys(normalize_allowed_users(usernames)))
    if not normalized:
        return
    user_rows = list((
        await conn.execute(select(users.c.id).where(users.c.username.in_(normalized)))
    ).scalars())
    if not user_rows:
        return
    await conn.execute(
        insert(branch_editors),
        [
            {"page_branch_id": branch_id, "user_id": user_id}
            for user_id in user_rows
        ],
    )


async def _record_user_edits(
    conn: Any, username: str, page_id: int, count: int
) -> None:
    if username == "Anonymous" or count <= 0:
        return
    user_id = await conn.scalar(select(users.c.id).where(users.c.username == username))
    if user_id is None:
        return
    await conn.execute(
        update(users)
        .where(users.c.id == user_id)
        .values(total_edits=users.c.total_edits + count)
    )
    statement = pg_insert(user_page_edit_counts).values(
        user_id=user_id,
        page_id=page_id,
        edit_count=count,
    )
    await conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_user_page_edit_counts_user_page",
            set_={
                "edit_count": user_page_edit_counts.c.edit_count
                + statement.excluded.edit_count
            },
        )
    )


def _revision_dict(row: Any) -> Dict[str, Any]:
    value = _row_dict(row)
    value["branch"] = value.pop("branch_name", value.pop("name", value.get("branch", "main")))
    value["author"] = value.pop("author_name", value.get("author", "Anonymous"))
    value["updated_at"] = value.get("updated_at", value.get("created_at"))
    return value


async def _find_branch(
    conn: Any, title: str, branch: str, *, for_update: bool = False
) -> Any:
    query = (
        select(page_branches, pages.c.title)
        .join(pages, page_branches.c.page_id == pages.c.id)
        .where(pages.c.title == title, _branch_name_column() == branch)
    )
    if for_update:
        query = query.with_for_update(of=page_branches)
    return (await conn.execute(query)).mappings().first()


async def _find_page(conn: Any, title: str) -> Any:
    return (await conn.execute(select(pages).where(pages.c.title == title))).mappings().first()


async def _authorize_edit(conn: Any, title: str, current: Any, actor: dict) -> dict:
    """Recheck current protection and account state inside the write transaction."""
    account = (await conn.execute(
        select(users).where(users.c.username == actor.get("username", ""))
    )).mappings().first()
    if account is None or not account["is_active"]:
        raise PagePermissionError("Authentication required.")
    flags = await conn.scalar(select(settings.c.value).where(settings.c.key == "feature_flags"))
    editing_enabled = (flags or {}).get("page_editing_enabled", FeatureFlags().page_editing_enabled)
    if not editing_enabled and not account["is_admin"]:
        raise PagePermissionError("Page editing is currently disabled by an administrator.")
    owner = await conn.scalar(
        select(users.c.username).where(func.lower(users.c.username) == title.lower()).limit(1)
    )
    if owner and account["username"].casefold() != owner.casefold():
        raise PagePermissionError("You can only edit your own user page.")
    page = dict(current) if current is not None else None
    if page is not None:
        allowed = await _allowed_users_by_branch(conn, [page["id"]])
        page["allowed_users"] = allowed.get(page["id"], [])
    if not can_user_edit_page(dict(account), page):
        raise PagePermissionError("You do not have permission to edit this page.")
    return dict(account)


async def _insert_page(
    conn: Any,
    title: str,
    content: str,
    author: str,
    branch: str,
    edit_summary: Optional[str],
    edit_permission: str,
    allowed_users: Optional[List[str]],
) -> Any:
    now = datetime.now(timezone.utc)
    page = await _find_page(conn, title)
    if page is None:
        page_result = await conn.execute(insert(pages).values(title=title, created_at=now).returning(pages.c.id))
        page_id = page_result.scalar_one()
    else:
        page_id = page["id"]

    if branch == "talk" and content.strip():
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = f"{content} ([[User:{author}]] {timestamp})"

    values = _branch_columns(
        {
            "page_id": page_id,
            "name": branch,
            "branch_name": branch,
            "content": content,
            "author": author,
            "author_name": author,
            "edit_summary": PageService._normalize_summary(edit_summary),
            "created_at": now,
            "updated_at": now,
            "edit_permission": edit_permission,
            "allowed_users": allowed_users or [],
        }
    )
    result = await conn.execute(
        insert(page_branches).values(**values).returning(page_branches.c.id)
    )
    branch_id = result.scalar_one()
    await _set_allowed_users(conn, branch_id, allowed_users)
    return branch_id


class PageService:
    """Service class for page-related operations."""

    @staticmethod
    def _normalize_summary(edit_summary: Optional[str]) -> str:
        return (edit_summary or "").strip()[:250]

    @staticmethod
    async def get_page(title: str, branch: str = "main") -> Optional[Dict[str, Any]]:
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().connect() as conn:
                row = await _find_branch(conn, title, branch)
                if row is None:
                    return None
                allowed = await _allowed_users_by_branch(conn, [row["id"]])
                return _page_dict(row, allowed.get(row["id"], []))
        except Exception as exc:
            logger.error(f"Error getting page {title} on branch {branch}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def create_page(
        title: str,
        content: str,
        author: str = "Anonymous",
        branch: str = "main",
        edit_summary: Optional[str] = None,
        edit_permission: str = "everybody",
        allowed_users: Optional[List[str]] = None,
    ) -> bool:
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                await _insert_page(
                    conn, title, content, author, branch, edit_summary,
                    edit_permission, allowed_users,
                )
            logger.info(f"Page created: {title} on branch: {branch} by {author}")
            return True
        except IntegrityError as exc:
            raise PageConflictError("That page branch already exists.") from exc
        except Exception as exc:
            logger.error(f"Error creating page {title} on branch {branch}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def update_page(
        title: str,
        content: str,
        author: str = "Anonymous",
        branch: str = "main",
        edit_summary: Optional[str] = None,
        edit_permission: str = "everybody",
        allowed_users: Optional[List[str]] = None,
        *,
        expected_version: str,
        actor: dict,
    ) -> bool:
        if not _ready():
            raise PageUnavailableError()
        summary = PageService._normalize_summary(edit_summary)
        now = datetime.now(timezone.utc)
        try:
            created_branches: List[str] = []
            async with _engine().begin() as conn:
                current = await _find_branch(conn, title, branch, for_update=True)
                if expected_version != page_version_token(current):
                    raise PageConflictError()
                actor = await _authorize_edit(conn, title, current, actor)
                # Protection changes are administrator-only, even if a caller
                # constructed its request before another admin protected the page.
                if not actor.get("is_admin", False):
                    edit_permission = current["edit_permission"] if current else "everybody"
                    allowed = await _allowed_users_by_branch(conn, [current["id"]]) if current else {}
                    allowed_users = allowed.get(current["id"], []) if current else []
                author = actor["username"]
                now = datetime.now(timezone.utc)
                if current:
                    await conn.execute(
                        insert(revisions).values(**_revision_columns({
                            "page_branch_id": current["id"],
                            "revision_number": await _next_revision_number(conn, current["id"]),
                            "content": current["content"],
                            "author_name": current.get("author_name", "Anonymous"),
                            "edited_by": author,
                            "created_at": current.get("updated_at", now),
                            "edit_summary": current.get("edit_summary", ""),
                        }))
                    )
                    new_content = content
                    if branch == "talk":
                        timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
                        new_content = f'{current["content"]}\n\n{content} ([[User:{author}]] {timestamp})'
                    values = _branch_columns(
                        {
                            "content": new_content,
                            "author_name": author,
                            "edit_summary": summary,
                            "edit_permission": edit_permission,
                            "allowed_users": allowed_users or [],
                            "updated_at": now,
                        }
                    )
                    await conn.execute(update(page_branches).where(page_branches.c.id == current["id"]).values(**values))
                    await _set_allowed_users(conn, current["id"], allowed_users)
                    await _record_user_edits(conn, author, current["page_id"], 1)
                elif await _find_page(conn, title) is None and branch == "main":
                    await _insert_page(conn, title, content, author, "main", summary, edit_permission, allowed_users)
                    await _insert_page(conn, title, "", author, "talk", "wikibot: Auto-created talk page", edit_permission, allowed_users)
                    page = await _find_page(conn, title)
                    await _record_user_edits(conn, author, page["id"], 1)
                    created_branches = ["main", "talk"]
                else:
                    await _insert_page(conn, title, content, author, branch, summary, edit_permission, allowed_users)
                    page = await _find_page(conn, title)
                    await _record_user_edits(conn, author, page["id"], 1)
                    created_branches = [branch]

            if created_branches:
                try:
                    await log_action(
                        author, "page_create", f"Page '{title}' created on branch 'main'",
                        category="page", metadata={"title": title, "branch": "main", "author": author},
                    )
                except Exception as log_error:
                    logger.warning(f"Page created but audit logging failed: {log_error}")
            logger.info(f"Page updated: {title} on branch: {branch} by {author}")
            return True
        except PageServiceError:
            raise
        except IntegrityError as exc:
            raise PageConflictError() from exc
        except Exception as exc:
            logger.error(f"Error updating page {title} on branch {branch}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def get_pages_by_branch(branch: str = "main", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        if not _ready():
            raise PageUnavailableError()
        try:
            query = (
                select(page_branches, pages.c.title)
                .join(pages, page_branches.c.page_id == pages.c.id)
                .where(_branch_name_column() == branch)
                .order_by(page_branches.c.updated_at.desc(), pages.c.title)
                .offset(offset)
            )
            if limit is not None:
                query = query.limit(limit)
            async with _engine().connect() as conn:
                rows = (await conn.execute(query)).mappings().all()
                allowed = await _allowed_users_by_branch(
                    conn, [row["id"] for row in rows]
                )
                return [_page_dict(row, allowed.get(row["id"], [])) for row in rows]
        except Exception as exc:
            logger.error(f"Error getting pages for branch {branch}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def search_pages(query: str, branch: str = "main", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        if not _ready():
            raise PageUnavailableError()
        try:
            escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            needle = f"%{escaped}%"
            rank = case(
                (func.lower(pages.c.title) == query.lower(), 0),
                (pages.c.title.ilike(f"{escaped}%", escape="!"), 1),
                else_=2,
            )
            statement = (
                select(page_branches, pages.c.title)
                .join(pages, page_branches.c.page_id == pages.c.id)
                .where(_branch_name_column() == branch, or_(pages.c.title.ilike(needle, escape="!"), page_branches.c.content.ilike(needle, escape="!")))
                .order_by(rank, page_branches.c.updated_at.desc(), pages.c.title)
                .limit(limit)
                .offset(offset)
            )
            async with _engine().connect() as conn:
                rows = (await conn.execute(statement)).mappings().all()
                allowed = await _allowed_users_by_branch(
                    conn, [row["id"] for row in rows]
                )
                return [_page_dict(row, allowed.get(row["id"], [])) for row in rows]
        except Exception as exc:
            logger.error(f"Error searching pages with query {query!r} on branch {branch!r}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def delete_page(title: str) -> bool:
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                result = await conn.execute(delete(pages).where(pages.c.title == title))
            if result.rowcount:
                logger.info(f"Page deleted (all branches): {title}")
                return True
            return False
        except Exception as exc:
            logger.error(f"Error deleting page {title}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def delete_branch(title: str, branch: str) -> bool:
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                current = await _find_branch(conn, title, branch, for_update=True)
                if not current:
                    return False
                result = await conn.execute(delete(page_branches).where(page_branches.c.id == current["id"]))
            return bool(result.rowcount)
        except Exception as exc:
            logger.error(f"Error deleting branch {branch} from page {title}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def rename_page(old_title: str, new_title: str) -> tuple[bool, str | None]:
        if old_title == new_title:
            return True, None
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                if await _find_page(conn, old_title) is None:
                    return False, "not_found"
                if await _find_page(conn, new_title) is not None:
                    return False, "conflict"
                result = await conn.execute(update(pages).where(pages.c.title == old_title).values(title=new_title))
                if not result.rowcount:
                    return False, "not_found"
            return True, None
        except IntegrityError:
            return False, "conflict"
        except Exception as exc:
            logger.error(f"Error renaming page {old_title} to {new_title}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def get_history(title: str, branch: str = "main", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return revisions newest-first, without the old Mongo 100-row ceiling."""
        if not _ready():
            raise PageUnavailableError()
        try:
            query = (
                select(revisions, pages.c.title, _branch_name_column().label("branch_name"))
                .join(page_branches, revisions.c.page_branch_id == page_branches.c.id)
                .join(pages, page_branches.c.page_id == pages.c.id)
                .where(pages.c.title == title, _branch_name_column() == branch)
                .order_by(_revision_time_column().desc(), revisions.c.id.desc())
            )
            if limit is not None:
                query = query.limit(limit)
            async with _engine().connect() as conn:
                rows = (await conn.execute(query)).mappings().all()
            return [_revision_dict(row) for row in rows]
        except Exception as exc:
            logger.error(f"Error getting history for {title} on branch {branch}: {exc}")
            raise PageUnavailableError() from exc

    @staticmethod
    async def get_revision(title: str, branch: str, revision_id: int) -> Optional[Dict[str, Any]]:
        """Read a stable revision identity scoped to its page and branch."""
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().connect() as conn:
                row = (await conn.execute(
                    select(revisions, pages.c.title, page_branches.c.name.label("branch_name"))
                    .join(page_branches, revisions.c.page_branch_id == page_branches.c.id)
                    .join(pages, page_branches.c.page_id == pages.c.id)
                    .where(pages.c.title == title, page_branches.c.name == branch, revisions.c.id == revision_id)
                )).mappings().first()
                return _revision_dict(row) if row is not None else None
        except Exception as exc:
            raise PageUnavailableError() from exc

    @staticmethod
    async def get_version(title: str, branch: str, index: int) -> Optional[Dict[str, Any]]:
        current = await PageService.get_page(title, branch)
        if index == 0:
            return current
        history = await PageService.get_history(title, branch)
        return history[index - 1] if 0 < index <= len(history) else None

    @staticmethod
    async def restore_version(
        title: str, branch: str, index: int, author: str, *, revision_id: int, actor: dict
    ) -> bool:
        """Restore a revision while recording the current value in the same transaction."""
        if not _ready():
            raise PageUnavailableError()
        try:
            async with _engine().begin() as conn:
                current = await _find_branch(conn, title, branch, for_update=True)
                if current is None:
                    return False
                actor = await _authorize_edit(conn, title, current, actor)
                author = actor["username"]
                history_query = (
                    select(revisions)
                    .where(revisions.c.page_branch_id == current["id"])
                    .order_by(_revision_time_column().desc(), revisions.c.id.desc())
                )
                history = (await conn.execute(history_query)).mappings().all()
                target = next((row for row in history if row["id"] == revision_id), None)
                if target is None:
                    return False
                await conn.execute(insert(revisions).values(**_revision_columns({
                    "page_branch_id": current["id"],
                    "revision_number": await _next_revision_number(conn, current["id"]),
                    "content": current["content"],
                    "author_name": current.get("author_name", "Anonymous"),
                    "edited_by": author,
                    "created_at": current.get("updated_at", datetime.now(timezone.utc)),
                    "edit_summary": current.get("edit_summary", ""),
                })))
                summary = f'Restored revision {target["revision_number"]}'
                original_summary = str(target.get("edit_summary") or "").strip()
                if original_summary:
                    summary = f"{summary} (original summary: {original_summary})"
                values = _branch_columns({
                    "content": target["content"],
                    "author_name": author,
                    "edit_summary": summary[:250],
                    "updated_at": datetime.now(timezone.utc),
                })
                await conn.execute(update(page_branches).where(page_branches.c.id == current["id"]).values(**values))
                await _record_user_edits(conn, author, current["page_id"], 1)
            return True
        except PageServiceError:
            raise
        except Exception as exc:
            logger.error(f"Error restoring version {index} for {title} on branch {branch}: {exc}")
            raise PageUnavailableError() from exc
