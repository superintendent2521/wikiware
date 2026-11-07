"""Regression coverage for policy and CSRF across page-content write routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

from src.config import SESSION_COOKIE_NAME, STATIC_DIR
from src.database import db_instance
from src.middleware import rate_limiter
from src.middleware.auth_middleware import AuthMiddleware
from src.routes.api import preview
from src.routes.web import branches, history, pages, user
from src.services.branch_service import BranchService
from src.services.page_errors import (
    PageConflictError,
    PagePermissionError,
    PageServiceError,
    PageUnavailableError,
)
from src.services.page_permissions import can_user_edit_page
from src.services.page_service import PageService
from src.services.settings_service import FeatureFlags
from src.services.user_service import UserService

WRITE_PATHS = [
    "/edit/Article",
    "/restore/Article/1",
    "/branches/Article/create",
    "/user/alice/edit",
]


@pytest.fixture
def editing(monkeypatch):
    """Exercise real HTTP handlers with isolated persistence and CSRF dependencies."""
    account = {"username": "alice", "is_admin": False, "total_edits": 0}
    page = {"content": "Current content", "edit_permission": "everybody", "version_token": "1:current"}
    monkeypatch.setattr(db_instance, "is_connected", True)
    monkeypatch.setattr(
        UserService, "get_user_by_session", AsyncMock(return_value=account)
    )
    monkeypatch.setattr(UserService, "get_user_by_username", AsyncMock(return_value=None))
    monkeypatch.setattr(UserService, "list_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(PageService, "get_page", AsyncMock(return_value=page))
    update = AsyncMock(return_value=True)
    restore = AsyncMock(return_value=True)
    create_branch = AsyncMock(return_value=True)
    monkeypatch.setattr(PageService, "update_page", update)
    monkeypatch.setattr(PageService, "restore_version", restore)
    monkeypatch.setattr(BranchService, "create_branch", create_branch)
    monkeypatch.setattr(
        PageService, "get_revision",
        AsyncMock(return_value={"id": 7, "content": "Old content"}),
    )
    monkeypatch.setattr(
        rate_limiter, "_rate_limiter", rate_limiter.RateLimiter(100, 60)
    )

    csrf = Mock()
    csrf.validate_csrf = AsyncMock()
    csrf.generate_csrf_tokens.return_value = ("token", "signed-token")
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.feature_flags = FeatureFlags()
    app.dependency_overrides[CsrfProtect] = lambda: csrf

    @app.exception_handler(PageServiceError)
    async def page_failure(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    @app.middleware("http")
    async def attach_settings(request, call_next):
        request.state.feature_flags = app.state.feature_flags
        request.state.global_banner = None
        request.state.csp_nonce = "test-nonce"
        return await call_next(request)

    for module in (pages, history, branches, user):
        app.include_router(module.router)
    app.include_router(preview.router, prefix="/api")

    with TestClient(app, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE_NAME, "test-session")
        yield SimpleNamespace(
            app=app, client=client, account=account, page=page, csrf=csrf,
            update=update, restore=restore, create_branch=create_branch,
        )


def post_edit(editing, path):
    return editing.client.post(
        path,
        data={
            "content": "New content", "edit_summary": "Test edit",
            "branch": "main", "branch_name": "draft", "source_branch": "main",
            "expected_version": "1:current", "revision_id": "7",
        },
    )


def assert_no_write(editing):
    editing.update.assert_not_awaited()
    editing.restore.assert_not_awaited()
    editing.create_branch.assert_not_awaited()


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("permission,edits", [("10_edits", 9), ("50_edits", 49)])
def test_edit_threshold_denies_writes(editing, path, permission, edits):
    editing.page["edit_permission"] = permission
    editing.account["total_edits"] = edits
    assert post_edit(editing, path).status_code == 403
    assert_no_write(editing)


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("permission,edits", [("10_edits", 10), ("50_edits", 50)])
def test_edit_threshold_accepts_qualified_users(editing, path, permission, edits):
    editing.page["edit_permission"] = permission
    editing.account["total_edits"] = edits
    assert post_edit(editing, path).status_code == 303
    assert sum(mock.await_count for mock in (
        editing.update, editing.restore, editing.create_branch
    )) == 1


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_editing_switch_blocks_writes(editing, path):
    editing.app.state.feature_flags = FeatureFlags(page_editing_enabled=False)
    assert post_edit(editing, path).status_code == 403
    assert_no_write(editing)


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_admin_can_edit_protected_pages_when_editing_is_disabled(editing, path):
    editing.account["is_admin"] = True
    editing.app.state.feature_flags = FeatureFlags(page_editing_enabled=False)
    editing.page.update(edit_permission="select_users", allowed_users=["bob"])
    assert post_edit(editing, path).status_code == 303


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("allowed_users", [["bob"], "bob, carol"])
def test_allowlist_denies_unlisted_users(editing, path, allowed_users):
    editing.page.update(edit_permission="select_users", allowed_users=allowed_users)
    assert post_edit(editing, path).status_code == 403
    assert_no_write(editing)


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("allowed_users", [["alice"], "bob, alice"])
def test_allowlist_accepts_listed_users(editing, path, allowed_users):
    editing.page.update(edit_permission="select_users", allowed_users=allowed_users)
    assert post_edit(editing, path).status_code == 303


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_csrf_failure_prevents_writes(editing, path):
    editing.csrf.validate_csrf.side_effect = CsrfProtectError(403, "Invalid CSRF token")
    assert post_edit(editing, path).status_code == 403
    assert_no_write(editing)


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("path", [
    "/edit/bob", "/restore/bob/1", "/branches/bob/create", "/user/bob/edit",
])
def test_personal_pages_remain_owner_only(editing, monkeypatch, path, is_admin):
    editing.account["is_admin"] = is_admin
    monkeypatch.setattr(
        UserService, "get_user_by_username", AsyncMock(return_value={"username": "bob"})
    )
    assert post_edit(editing, path).status_code == 403
    assert_no_write(editing)


@pytest.mark.parametrize("allowed_users", [["alice"], "alice"])
def test_personal_page_save_preserves_protection(editing, allowed_users):
    editing.page.update(edit_permission="select_users", allowed_users=allowed_users)
    assert post_edit(editing, "/user/alice/edit").status_code == 303
    assert editing.update.await_args.kwargs["edit_permission"] == "select_users"
    assert editing.update.await_args.kwargs["allowed_users"] == ["alice"]


def test_unknown_protection_mode_denies_edits():
    assert not can_user_edit_page(
        {"username": "alice", "total_edits": 100}, {"edit_permission": "unknown"}
    )


def test_anonymous_user_cannot_create_page():
    assert not can_user_edit_page({}, None)


def test_authentication_keeps_edit_count(editing):
    editing.account["total_edits"] = 50

    @editing.app.get("/test-current-user")
    async def current_user(request: Request):
        first = await AuthMiddleware.get_current_user(request)
        second = await AuthMiddleware.get_current_user(request)
        assert first == second
        return first

    payload = editing.client.get("/test-current-user").json()
    assert payload == {"username": "alice", "is_admin": False, "total_edits": 50}
    UserService.get_user_by_session.assert_awaited_once()
    editing.client.get("/test-current-user")
    assert UserService.get_user_by_session.await_count == 2


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_save_forwards_version_and_authenticated_actor(editing, path):
    assert post_edit(editing, path).status_code == 303
    assert editing.update.await_args.kwargs["expected_version"] == "1:current"
    assert editing.update.await_args.kwargs["actor"]["username"] == "alice"


def test_restore_forwards_stable_revision_identity(editing):
    assert post_edit(editing, "/restore/Article/1").status_code == 303
    assert editing.restore.await_args.kwargs["revision_id"] == 7
    assert editing.restore.await_args.kwargs["actor"]["username"] == "alice"


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
@pytest.mark.parametrize("error", [PageConflictError(), PageUnavailableError(), PagePermissionError("Denied")])
def test_json_save_preserves_error_status(editing, path, error):
    editing.update.side_effect = error
    response = editing.client.post(path, headers={"Accept": "application/json"}, data={
        "content": "Unsaved text", "edit_summary": "Test", "expected_version": "1:current",
    })
    assert response.status_code == error.status_code
    assert response.json()["error"] == str(error)


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_json_success_acknowledges_persistence(editing, path):
    response = editing.client.post(path, headers={"Accept": "application/json"}, data={
        "content": "Saved text", "edit_summary": "Test", "expected_version": "1:current",
    })
    assert response.status_code == 200
    assert response.json()["redirect_url"].startswith("http://testserver/")
    editing.update.assert_awaited_once()


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_save_without_starting_version_never_writes(editing, path):
    response = editing.client.post(path, data={"content": "Unsaved", "edit_summary": "Test"})
    assert response.status_code == 422
    assert_no_write(editing)


@pytest.mark.parametrize("error", [PagePermissionError("Denied"), PageUnavailableError()])
def test_restore_preserves_transaction_failure_status(editing, error):
    editing.restore.side_effect = error
    assert post_edit(editing, "/restore/Article/1").status_code == error.status_code


def test_preview_uses_published_markdown_dialect(editing):
    response = editing.client.post("/api/preview", data={"content": "# Heading\n\n[[Ship:draft]]"})
    assert response.status_code == 200
    assert 'href="/page/Ship?branch=draft"' in response.json()["html"]
    assert response.json()["sources"] == []
    assert_no_write(editing)


def test_preview_requires_authentication(editing):
    editing.client.cookies.clear()
    response = editing.client.post("/api/preview", data={"content": "Text"})
    assert response.status_code == 401


def test_preview_validates_csrf_before_rendering(editing, monkeypatch):
    renderer = AsyncMock()
    monkeypatch.setattr(preview, "render_markdown", renderer)
    editing.csrf.validate_csrf.side_effect = CsrfProtectError(403, "Invalid CSRF token")
    assert editing.client.post("/api/preview", data={"content": "Text"}).status_code == 403
    renderer.assert_not_awaited()


def test_preview_rejects_oversized_content(editing):
    response = editing.client.post("/api/preview", data={"content": "x" * (1024 * 1024 + 1)})
    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_html_save_failure_keeps_submitted_content_recoverable(editing, path):
    editing.update.side_effect = PageConflictError()
    response = editing.client.post(path, data={
        "content": "<script>unsaved</script> & original", "edit_summary": "Test",
        "expected_version": "1:current",
    })
    assert response.status_code == 409
    assert "&lt;script&gt;unsaved&lt;/script&gt; &amp; original" in response.text
    assert "<script>unsaved</script>" not in response.text


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_anonymous_requests_never_mutate_content(editing, path):
    editing.client.cookies.clear()
    response = post_edit(editing, path)
    assert response.status_code in {401, 303}
    if response.status_code == 303:
        assert response.headers["location"] == "/login"
    assert_no_write(editing)


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_editor_can_intentionally_save_empty_content(editing, path):
    response = editing.client.post(path, headers={"Accept": "application/json"}, data={
        "content": "", "edit_summary": "Clear article", "expected_version": "1:current",
    })
    assert response.status_code == 200
    assert editing.update.await_args.args[1] == ""


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_early_editing_disabled_denial_returns_json_to_editor(editing, path):
    editing.app.state.feature_flags = FeatureFlags(page_editing_enabled=False)
    response = editing.client.post(path, headers={"Accept": "application/json"}, data={
        "content": "Keep my work", "edit_summary": "Test", "expected_version": "1:current",
    })
    assert response.status_code == 403
    assert "disabled" in response.json()["error"]
    assert_no_write(editing)


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_early_denial_keeps_html_submission_recoverable(editing, path):
    editing.app.state.feature_flags = FeatureFlags(page_editing_enabled=False)
    response = editing.client.post(path, data={
        "content": "Keep this exact unsaved text", "edit_summary": "Test", "expected_version": "1:current",
    })
    assert response.status_code == 403
    assert "Keep this exact unsaved text" in response.text
    assert_no_write(editing)


@pytest.mark.parametrize("path", [
    "/page/Article", "/edit/Article", "/user/alice", "/user/alice/edit", "/history/Article",
])
def test_database_failure_never_renders_a_blank_editor(editing, path):
    PageService.get_page.side_effect = PageUnavailableError()
    response = editing.client.get(path)
    assert response.status_code == 503


@pytest.mark.parametrize("path", ["/edit/Article", "/user/alice/edit"])
def test_editor_form_identifies_the_loaded_version(editing, path):
    response = editing.client.get(path)
    assert response.status_code == 200
    assert 'name="expected_version" value="1:current"' in response.text
