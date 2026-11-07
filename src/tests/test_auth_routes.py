"""Account workflows without a live server or persistent test users."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

from src.config import SESSION_COOKIE_NAME
from src.database import db_instance
from src.middleware import rate_limiter
from src.middleware.auth_middleware import AuthMiddleware
from src.routes.web import auth
from src.services.settings_service import FeatureFlags
from src.services.user_service import UserService


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setattr(db_instance, "is_connected", True)
    create_user = AsyncMock(return_value={"username": "alice"})
    authenticate = AsyncMock(return_value={"username": "alice"})
    create_session = AsyncMock(return_value="new-session")
    delete_session = AsyncMock(return_value=True)
    monkeypatch.setattr(UserService, "create_user", create_user)
    monkeypatch.setattr(UserService, "authenticate_user", authenticate)
    monkeypatch.setattr(UserService, "create_session", create_session)
    monkeypatch.setattr(UserService, "delete_session", delete_session)
    monkeypatch.setattr(AuthMiddleware, "get_current_user", AsyncMock(return_value=None))
    monkeypatch.setattr(rate_limiter, "_rate_limiter", rate_limiter.RateLimiter(100, 60))

    def render_form(name, context, status_code=200):
        return JSONResponse({"template": name, "error": context.get("error")}, status_code=status_code)

    monkeypatch.setattr(auth, "templates", SimpleNamespace(TemplateResponse=render_form))
    csrf = Mock()
    csrf.validate_csrf = AsyncMock()
    csrf.generate_csrf_tokens.return_value = ("token", "signed-token")
    app = FastAPI()
    app.state.feature_flags = FeatureFlags()
    app.dependency_overrides[CsrfProtect] = lambda: csrf

    @app.middleware("http")
    async def attach_flags(request, call_next):
        request.state.feature_flags = app.state.feature_flags
        return await call_next(request)

    app.include_router(auth.router)
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(
            client=client, app=app, create_user=create_user, authenticate=authenticate,
            create_session=create_session, delete_session=delete_session, csrf=csrf,
        )


def test_registration_creates_account_then_session(accounts):
    response = accounts.client.post("/register", data={
        "username": "alice", "password": "test-password", "confirm_password": "test-password",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.cookies[SESSION_COOKIE_NAME] == "new-session"
    accounts.csrf.validate_csrf.assert_awaited_once()
    accounts.create_user.assert_awaited_once()
    accounts.create_session.assert_awaited_once_with("alice")


def test_mismatched_passwords_do_not_create_accounts(accounts):
    response = accounts.client.post("/register", data={
        "username": "alice", "password": "test-password", "confirm_password": "different",
    })
    assert response.json()["error"] == "Passwords do not match"
    accounts.create_user.assert_not_awaited()
    accounts.create_session.assert_not_awaited()


def test_disabled_registration_does_not_create_accounts(accounts):
    accounts.app.state.feature_flags = FeatureFlags(account_creation_enabled=False)
    response = accounts.client.post("/register", data={
        "username": "alice", "password": "test-password", "confirm_password": "test-password",
    })
    assert response.status_code == 403
    accounts.create_user.assert_not_awaited()
    accounts.create_session.assert_not_awaited()


@pytest.mark.parametrize("target,expected", [
    ("/page/Article?branch=draft", "/page/Article?branch=draft"),
    ("https://outside.example.test", "/"),
    ("//outside.example.test", "/"),
])
def test_login_sets_session_and_keeps_redirect_internal(accounts, target, expected):
    response = accounts.client.post("/login", data={
        "username": "alice", "password": "test-password", "next": target,
    })
    assert response.status_code == 303
    assert response.headers["location"] == expected
    assert response.cookies[SESSION_COOKIE_NAME] == "new-session"
    assert "HttpOnly" in response.headers["set-cookie"]
    accounts.csrf.validate_csrf.assert_awaited_once()


def test_invalid_login_never_creates_session(accounts):
    accounts.authenticate.return_value = None
    response = accounts.client.post("/login", data={"username": "alice", "password": "wrong"})
    assert response.json()["error"] == "Invalid username or password"
    assert SESSION_COOKIE_NAME not in response.cookies
    accounts.create_session.assert_not_awaited()


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_invalid_csrf_cannot_create_accounts_or_sessions(accounts, path):
    accounts.csrf.validate_csrf.side_effect = CsrfProtectError(403, "Invalid CSRF token")
    response = accounts.client.post(path, data={
        "username": "alice", "password": "test-password", "confirm_password": "test-password",
    })
    assert response.status_code != 303
    accounts.create_user.assert_not_awaited()
    accounts.authenticate.assert_not_awaited()
    accounts.create_session.assert_not_awaited()


def test_logout_invalidates_session_and_expires_cookie(accounts):
    accounts.client.cookies.set(SESSION_COOKIE_NAME, "existing-session")
    response = accounts.client.post("/logout")
    assert response.status_code == 303
    accounts.csrf.validate_csrf.assert_awaited_once()
    accounts.delete_session.assert_awaited_once_with("existing-session")
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_invalid_csrf_cannot_invalidate_a_session(accounts):
    accounts.client.cookies.set(SESSION_COOKIE_NAME, "existing-session")
    accounts.csrf.validate_csrf.side_effect = CsrfProtectError(403, "Invalid CSRF token")
    accounts.client.post("/logout")
    accounts.delete_session.assert_not_awaited()
