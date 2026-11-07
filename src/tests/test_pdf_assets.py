"""PDF generation must never fetch authored network or filesystem locations."""

from unittest.mock import AsyncMock

import pytest

from src.routes.api import pdf
from src.services.storage_service import StorageError

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("source", [
    "https://outside.example.test/image.png", "http://127.0.0.1/private.png",
    "file:///private/image.png", "//outside.example.test/image.png",
    "/etc/private.png", "../private.png", "/static/uploads/../private.png",
    "/static/uploads/%2e%2e%2fprivate.png", "/media/uploads/subfolder/image.png",
    "/media/uploads/image.svg", "/static/uploads/image%00.png", "http://[",
])
async def test_pdf_rejects_untrusted_image_locations(monkeypatch, source):
    download = AsyncMock(return_value=b"image")
    monkeypatch.setattr(pdf, "download_image_bytes", download)

    rendered, assets = await pdf._pdf_images(f'<p><img src="{source}" alt="Image"></p>')

    assert assets == {}
    assert "Image unavailable in PDF" in rendered
    assert "<img" not in rendered
    download.assert_not_awaited()


@pytest.mark.parametrize("prefix", ["/media/uploads/", "/static/uploads/"])
async def test_pdf_embeds_only_storage_bytes_under_generated_asset_names(monkeypatch, prefix):
    download = AsyncMock(return_value=b"image-content")
    monkeypatch.setattr(pdf, "download_image_bytes", download)

    rendered, assets = await pdf._pdf_images(f'<img src="{prefix}my%20image.png" alt="Caption">')

    assert assets == {"image-0.png": b"image-content"}
    assert 'src="image-0.png"' in rendered
    assert 'alt="Caption"' in rendered
    download.assert_awaited_once_with("my image.png")


async def test_pdf_missing_storage_image_does_not_fail_the_whole_export(monkeypatch):
    monkeypatch.setattr(pdf, "download_image_bytes", AsyncMock(side_effect=StorageError("Missing")))
    rendered, assets = await pdf._pdf_images('<img src="/media/uploads/missing.png">')
    assert assets == {}
    assert "Image unavailable in PDF" in rendered


async def test_pdf_reuses_duplicate_images_without_exhausting_asset_limit(monkeypatch):
    download = AsyncMock(return_value=b"same-image")
    monkeypatch.setattr(pdf, "download_image_bytes", download)
    content = '<img src="/media/uploads/same.png">' * 40

    rendered, assets = await pdf._pdf_images(content)

    assert len(assets) == 1
    assert rendered.count('src="image-0.png"') == 40
    download.assert_awaited_once_with("same.png")


async def test_pdf_caps_distinct_embedded_images(monkeypatch):
    download = AsyncMock(return_value=b"image")
    monkeypatch.setattr(pdf, "download_image_bytes", download)
    content = "".join(f'<img src="/media/uploads/image-{index}.png">' for index in range(33))

    rendered, assets = await pdf._pdf_images(content)

    assert len(assets) == 32
    assert rendered.count("Image unavailable in PDF") == 1
    assert download.await_count == 32


async def test_pdf_caps_total_embedded_image_bytes(monkeypatch):
    monkeypatch.setattr(pdf, "download_image_bytes", AsyncMock(return_value=b"x" * (8 * 1024 * 1024)))
    content = "".join(f'<img src="/media/uploads/image-{index}.png">' for index in range(5))

    rendered, assets = await pdf._pdf_images(content)

    assert sum(len(data) for data in assets.values()) == 32 * 1024 * 1024
    assert rendered.count("Image unavailable in PDF") == 1


async def test_pdf_rejects_an_individual_oversized_image(monkeypatch):
    monkeypatch.setattr(pdf, "download_image_bytes", AsyncMock(return_value=b"x" * (8 * 1024 * 1024 + 1)))
    rendered, assets = await pdf._pdf_images('<img src="/media/uploads/large.png">')
    assert assets == {}
    assert "Image unavailable in PDF" in rendered
