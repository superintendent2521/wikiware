"""Exercise revision and policy guarantees against real PostgreSQL transactions."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, insert, select, update

from src.database import db_instance
from src.schema import page_branches, pages, revisions, settings, users
from src.services import page_service
from src.services.branch_service import BranchService
from src.services.page_errors import (
    PageConflictError,
    PagePermissionError,
    PageUnavailableError,
)
from src.services.page_service import PageService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ACTOR = {"username": "alice", "is_admin": False, "total_edits": 0}


async def seed_page(engine, *, title="Article", branch="main", content="Original"):
    async with engine.begin() as connection:
        await connection.execute(insert(users).values(username="alice", password_hash="hash"))
        page_id = await connection.scalar(insert(pages).values(title=title).returning(pages.c.id))
        branch_id = await connection.scalar(insert(page_branches).values(
            page_id=page_id, name=branch, content=content, author_name="alice",
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        ).returning(page_branches.c.id))
    return page_id, branch_id


async def save(content, expected_version):
    return await PageService.update_page(
        "Article", content, "alice", "main", edit_summary="Test change",
        expected_version=expected_version, actor=ACTOR,
    )


async def test_successful_save_archives_exact_previous_content(postgres):
    await seed_page(postgres)
    before = await PageService.get_page("Article")

    assert await save("Updated", before["version_token"])

    after = await PageService.get_page("Article")
    history = await PageService.get_history("Article")
    assert after["content"] == "Updated"
    assert after["version_token"] != before["version_token"]
    assert len(history) == 1
    assert history[0]["content"] == "Original"
    assert history[0]["author"] == "alice"
    async with postgres.connect() as connection:
        assert await connection.scalar(select(users.c.total_edits)) == 1


async def test_stale_save_does_not_overwrite_or_add_a_revision(postgres):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    await save("First writer", token)

    with pytest.raises(PageConflictError):
        await save("Stale writer", token)

    assert (await PageService.get_page("Article"))["content"] == "First writer"
    assert len(await PageService.get_history("Article")) == 1


async def test_simultaneous_saves_allow_exactly_one_writer(postgres):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]

    results = await asyncio.gather(save("One", token), save("Two", token), return_exceptions=True)

    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, PageConflictError) for result in results) == 1
    assert (await PageService.get_page("Article"))["content"] in {"One", "Two"}
    assert len(await PageService.get_history("Article")) == 1


async def test_second_creator_cannot_overwrite_a_new_page(postgres):
    async with postgres.begin() as connection:
        await connection.execute(insert(users).values(username="alice", password_hash="hash"))
    await save("Created first", "new")

    with pytest.raises(PageConflictError):
        await save("Created second", "new")

    assert (await PageService.get_page("Article"))["content"] == "Created first"


async def test_policy_is_rechecked_from_database_inside_save(postgres):
    _, branch_id = await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    async with postgres.begin() as connection:
        await connection.execute(update(page_branches).where(page_branches.c.id == branch_id)
                                 .values(edit_permission="select_users"))

    with pytest.raises(PagePermissionError):
        await PageService.update_page(
            "Article", "Blocked", "alice", expected_version=token,
            actor={"username": "alice", "is_admin": True, "total_edits": 100},
        )

    assert (await PageService.get_page("Article"))["content"] == "Original"
    assert await PageService.get_history("Article") == []


async def test_editing_switch_is_rechecked_inside_save(postgres):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    async with postgres.begin() as connection:
        await connection.execute(insert(settings).values(
            key="feature_flags", value={"page_editing_enabled": False}
        ))

    with pytest.raises(PagePermissionError):
        await save("Blocked", token)

    assert (await PageService.get_page("Article"))["content"] == "Original"


async def test_save_rolls_back_content_history_and_counts_on_failure(postgres, monkeypatch):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    monkeypatch.setattr(page_service, "_record_user_edits", AsyncMock(side_effect=RuntimeError("failed")))

    with pytest.raises(PageUnavailableError):
        await save("Must roll back", token)

    assert (await PageService.get_page("Article"))["content"] == "Original"
    assert await PageService.get_history("Article") == []
    async with postgres.connect() as connection:
        assert await connection.scalar(select(users.c.total_edits)) == 0


async def test_restore_uses_stable_revision_id_after_new_edits(postgres):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    await save("First edit", token)
    original = (await PageService.get_history("Article"))[0]
    token = (await PageService.get_page("Article"))["version_token"]
    await save("Second edit", token)

    assert await PageService.restore_version(
        "Article", "main", 1, "alice", revision_id=original["id"], actor=ACTOR
    )

    assert (await PageService.get_page("Article"))["content"] == "Original"
    history = await PageService.get_history("Article")
    assert len(history) == 3
    assert history[0]["content"] == "Second edit"
    assert (await PageService.get_revision("Article", "main", original["id"]))["content"] == "Original"


async def test_restore_rejects_a_revision_from_another_branch(postgres):
    page_id, _ = await seed_page(postgres)
    async with postgres.begin() as connection:
        other_branch = await connection.scalar(insert(page_branches).values(
            page_id=page_id, name="draft", content="Draft"
        ).returning(page_branches.c.id))
        other_revision = await connection.scalar(insert(revisions).values(
            page_branch_id=other_branch, revision_number=1, content="Private draft"
        ).returning(revisions.c.id))

    assert not await PageService.restore_version(
        "Article", "main", 1, "alice", revision_id=other_revision, actor=ACTOR
    )
    assert await PageService.get_revision("Article", "main", other_revision) is None
    assert (await PageService.get_page("Article"))["content"] == "Original"
    assert await PageService.get_history("Article") == []


async def test_branch_creation_copies_more_than_one_hundred_revisions(postgres):
    _, branch_id = await seed_page(postgres)
    async with postgres.begin() as connection:
        await connection.execute(insert(revisions), [
            {"page_branch_id": branch_id, "revision_number": number, "content": f"Revision {number}"}
            for number in range(1, 106)
        ])

    assert await BranchService.create_branch("Article", "draft", "main", actor=ACTOR)

    assert len(await PageService.get_history("Article", "draft")) == 105
    assert (await PageService.get_page("Article", "draft"))["content"] == "Original"


async def test_missing_page_is_distinct_from_database_unavailability(postgres, monkeypatch):
    assert await PageService.get_page("Missing") is None
    monkeypatch.setattr(db_instance, "is_connected", False)
    with pytest.raises(PageUnavailableError):
        await PageService.get_page("Missing")


async def test_delete_page_cascades_through_branches_and_revisions(postgres):
    await seed_page(postgres)
    await save("Edit", (await PageService.get_page("Article"))["version_token"])

    assert await PageService.delete_page("Article")

    async with postgres.connect() as connection:
        for table in (pages, page_branches, revisions):
            assert await connection.scalar(select(func.count()).select_from(table)) == 0


async def test_protection_with_unknown_usernames_does_not_insert_empty_editor_rows(postgres):
    await seed_page(postgres)
    async with postgres.begin() as connection:
        await connection.execute(update(users).where(users.c.username == "alice").values(is_admin=True))
    token = (await PageService.get_page("Article"))["version_token"]

    assert await PageService.update_page(
        "Article", "Protected", "alice", expected_version=token, actor=ACTOR,
        edit_permission="select_users", allowed_users=["does_not_exist"],
    )

    page = await PageService.get_page("Article")
    assert page["edit_permission"] == "select_users"
    assert page["allowed_users"] == []


async def test_intentionally_empty_content_is_saved_without_losing_previous_revision(postgres):
    await seed_page(postgres)
    token = (await PageService.get_page("Article"))["version_token"]
    assert await save("", token)
    assert (await PageService.get_page("Article"))["content"] == ""
    assert (await PageService.get_history("Article"))[0]["content"] == "Original"
