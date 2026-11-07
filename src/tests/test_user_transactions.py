"""Persistent account, session, and favorite flows against PostgreSQL."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from src.models.user import UserRegistration
from src.schema import favorites, sessions, users
from src.services.page_service import PageService
from src.services.user_service import UserService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def create_account():
    account = await UserService.create_user(UserRegistration(
        username="alice", password="test-original-password",
    ))
    assert account is not None
    return account


async def test_registered_account_is_committed_and_can_authenticate(postgres):
    account = await create_account()

    persisted = await UserService.get_user_by_username("alice")
    assert persisted is not None
    assert persisted["id"] == account["id"]
    assert persisted["password_hash"] != "test-original-password"
    assert await UserService.authenticate_user("alice", "test-original-password") is not None
    assert await UserService.authenticate_user("alice", "wrong-password") is None
    async with postgres.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(users)) == 1


async def test_sessions_persist_and_logout_invalidates_them(postgres):
    await create_account()
    session_id = await UserService.create_session("alice")
    assert session_id
    account = await UserService.get_user_by_session(session_id)
    assert account is not None
    assert account["username"] == "alice"

    assert await UserService.delete_session(session_id)

    assert await UserService.get_user_by_session(session_id) is None


async def test_expired_sessions_are_rejected_and_removed(postgres):
    await create_account()
    session_id = await UserService.create_session("alice")
    assert session_id
    async with postgres.begin() as connection:
        await connection.execute(update(sessions).where(sessions.c.session_id == session_id)
                                 .values(expires_at=datetime.now(timezone.utc) - timedelta(days=1)))

    assert await UserService.get_user_by_session(session_id) is None

    async with postgres.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(sessions)) == 0


async def test_password_change_checks_current_password_and_persists_new_hash(postgres):
    await create_account()
    success, reason = await UserService.change_password("alice", "wrong", "new-password")
    assert not success
    assert reason == "invalid_current_password"
    assert await UserService.authenticate_user("alice", "test-original-password") is not None

    success, reason = await UserService.change_password(
        "alice", "test-original-password", "new-password"
    )

    assert success
    assert reason == ""
    assert await UserService.authenticate_user("alice", "test-original-password") is None
    assert await UserService.authenticate_user("alice", "new-password") is not None
    assert (await UserService.get_user_by_username("alice"))["password_changed_at"] is not None


async def test_favorites_follow_renames_and_cascade_on_deletion(postgres):
    account = await create_account()
    assert await PageService.update_page(
        "Article", "Content", "alice", expected_version="new", actor=account,
    )
    assert await UserService.add_favorite("alice", "Article")
    assert await UserService.add_favorite("alice", "Article")
    async with postgres.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(favorites)) == 1

    assert await PageService.rename_page("Article", "Renamed") == (True, None)
    assert await UserService.list_favorites("alice") == [{"title": "Renamed", "branch": "main"}]
    assert await PageService.delete_page("Renamed")
    assert await UserService.list_favorites("alice") == []


async def test_password_change_revokes_preexisting_sessions(postgres):
    await create_account()
    old_session = await UserService.create_session("alice")
    assert old_session
    assert await UserService.get_user_by_session(old_session) is not None

    success, _ = await UserService.change_password(
        "alice", "test-original-password", "new-password"
    )

    assert success
    assert await UserService.get_user_by_session(old_session) is None
    new_session = await UserService.create_session("alice")
    assert new_session
    assert await UserService.get_user_by_session(new_session) is not None
