"""Validate log-stream handshakes without opening sockets or installing log sinks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import SESSION_COOKIE_NAME
from src.database import db_instance
from src.services import log_streamer
from src.services.user_service import UserService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def handshake(monkeypatch):
    account = {"username": "alice", "is_admin": True, "is_active": True}
    get_user = AsyncMock(return_value=account)
    monkeypatch.setattr(UserService, "get_user_by_session", get_user)
    monkeypatch.setattr(db_instance, "is_connected", True)
    websocket = SimpleNamespace(
        headers={"origin": "https://wiki.example.test", "host": "wiki.example.test"},
        cookies={SESSION_COOKIE_NAME: "test-session"}, url=SimpleNamespace(scheme="wss"),
    )
    return SimpleNamespace(websocket=websocket, account=account, get_user=get_user)


async def test_same_origin_active_admin_can_view_logs(handshake):
    assert await log_streamer._authenticate_websocket(handshake.websocket)
    handshake.get_user.assert_awaited_once_with("test-session")


@pytest.mark.parametrize("account", [None, {"is_admin": False}, {"is_admin": True, "is_active": False}])
async def test_non_admin_or_inactive_sessions_cannot_view_logs(handshake, account):
    handshake.get_user.return_value = account
    assert not await log_streamer._authenticate_websocket(handshake.websocket)


@pytest.mark.parametrize("origin", [
    "https://outside.example.test", "null", "https://wiki.example.test.attacker.test",
    "http://wiki.example.test", "file:///wiki.example.test",
])
async def test_cross_origin_handshakes_are_rejected_before_session_lookup(handshake, origin):
    handshake.websocket.headers["origin"] = origin
    assert not await log_streamer._authenticate_websocket(handshake.websocket)
    handshake.get_user.assert_not_awaited()


async def test_missing_session_cookie_is_rejected(handshake):
    handshake.websocket.cookies.clear()
    assert not await log_streamer._authenticate_websocket(handshake.websocket)
    handshake.get_user.assert_not_awaited()


async def test_database_outage_rejects_the_handshake(handshake, monkeypatch):
    monkeypatch.setattr(db_instance, "is_connected", False)
    assert not await log_streamer._authenticate_websocket(handshake.websocket)
    handshake.get_user.assert_not_awaited()


async def test_session_lookup_failure_does_not_open_a_log_stream(handshake):
    handshake.get_user.side_effect = RuntimeError("Database unavailable")
    assert not await log_streamer._authenticate_websocket(handshake.websocket)
