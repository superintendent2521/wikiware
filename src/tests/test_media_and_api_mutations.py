"""Secondary browser endpoints enforce CSRF before mutating external state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

from src.database import db_instance
from src.middleware import rate_limiter
from src.middleware.auth_middleware import AuthMiddleware
from src.routes.api import favorites, images, pdf
from src.routes.web import images as web_images
from src.services.analytics_service import AnalyticsService
from src.services.user_service import UserService

MUTATIONS = [
    ("POST", "/api/favorites/Article"),
    ("DELETE", "/api/favorites/Article"),
    ("DELETE", "/api/images/photo.png"),
    ("POST", "/api/pdf/page"),
]


@pytest.fixture
def media_api(monkeypatch):
    account = {"username": "alice", "is_admin": True}
    monkeypatch.setattr(db_instance, "is_connected", True)
    monkeypatch.setattr(AuthMiddleware, "get_current_user", AsyncMock(return_value=account))
    monkeypatch.setattr(AuthMiddleware, "require_auth", AsyncMock(return_value=account))
    monkeypatch.setattr(rate_limiter, "_rate_limiter", rate_limiter.RateLimiter(100, 60))
    monkeypatch.setattr(UserService, "list_favorites", AsyncMock(return_value=[]))
    add = AsyncMock(return_value=True)
    remove = AsyncMock(return_value=True)
    delete_image = AsyncMock()
    collect_pdf = AsyncMock(return_value=[{
        "title": "Article", "branch": "main", "raw": "Text", "content": "<p>Text</p>",
    }])
    monkeypatch.setattr(UserService, "add_favorite", add)
    monkeypatch.setattr(UserService, "remove_favorite", remove)
    monkeypatch.setattr(favorites.PageService, "get_page", AsyncMock(return_value={"content": "Text"}))
    monkeypatch.setattr(AnalyticsService, "record_favorite_added", AsyncMock())
    monkeypatch.setattr(AnalyticsService, "record_favorite_removed", AsyncMock())
    monkeypatch.setattr(images, "storage_image_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(images, "storage_delete_image", delete_image)
    monkeypatch.setattr(images, "execute", AsyncMock(return_value=1))
    monkeypatch.setattr(images, "log_action", AsyncMock())
    monkeypatch.setattr(pdf, "_collect_linked_pages", collect_pdf)
    monkeypatch.setattr(pdf, "_pdf_images", AsyncMock(return_value=("<p>Text</p>", {})))
    monkeypatch.setattr(pdf, "run_in_threadpool", AsyncMock(return_value=b"%PDF-test"))
    list_images = AsyncMock(return_value=[
        {"filename": "Ship.PNG", "url": "/media/uploads/Ship.PNG"},
        {"filename": "Engine.png", "url": "/media/uploads/Engine.png"},
    ])
    monkeypatch.setattr(web_images, "_list_images", list_images)
    monkeypatch.setattr(web_images, "templates", SimpleNamespace(
        TemplateResponse=lambda name, context: JSONResponse({"images": context["images"]}),
    ))
    csrf = Mock()
    csrf.validate_csrf = AsyncMock()
    csrf.generate_csrf_tokens.return_value = ("token", "signed-token")
    app = FastAPI()
    app.dependency_overrides[CsrfProtect] = lambda: csrf
    for module in (favorites, images, pdf):
        app.include_router(module.router, prefix="/api")
    app.include_router(web_images.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, account=account, csrf=csrf, add=add, remove=remove,
            delete_image=delete_image, collect_pdf=collect_pdf, list_images=list_images,
        )


@pytest.mark.parametrize("method,path", MUTATIONS)
def test_csrf_failure_blocks_secondary_mutations(media_api, method, path):
    media_api.csrf.validate_csrf.side_effect = CsrfProtectError(403, "Invalid CSRF token")
    response = media_api.client.request(method, path, json={"title": "Article"})
    assert response.status_code == 403
    media_api.add.assert_not_awaited()
    media_api.remove.assert_not_awaited()
    media_api.delete_image.assert_not_awaited()
    media_api.collect_pdf.assert_not_awaited()


@pytest.mark.parametrize("method,path", MUTATIONS)
def test_validated_secondary_mutations_still_work(media_api, method, path):
    response = media_api.client.request(method, path, json={"title": "Article"})
    assert response.status_code == 200
    media_api.csrf.validate_csrf.assert_awaited_once()
    assert sum(mock.await_count for mock in (
        media_api.add, media_api.remove, media_api.delete_image, media_api.collect_pdf,
    )) == 1


def test_non_admin_cannot_delete_images(media_api):
    media_api.account["is_admin"] = False
    response = media_api.client.delete("/api/images/photo.png")
    assert response.status_code == 403
    media_api.delete_image.assert_not_awaited()


def test_web_image_library_awaits_listing_and_filters_names(media_api):
    response = media_api.client.get("/images?q=ship")
    assert response.status_code == 200
    assert response.json()["images"] == [{"filename": "Ship.PNG", "url": "/media/uploads/Ship.PNG"}]
    media_api.list_images.assert_awaited_once()
