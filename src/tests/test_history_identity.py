"""Recent-history widgets retain stable identity and real revision numbering."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.routes.api.history import _format_version_entry
from src.routes.web.history import _build_version_entries, _fetch_versions_for_history
from src.services.page_service import PageService


@pytest.mark.asyncio
async def test_limited_history_labels_current_version_from_real_revision_numbers(monkeypatch):
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(PageService, "get_page", AsyncMock(return_value={
        "id": 5, "content": "Current", "author": "alice", "updated_at": timestamp,
    }))
    monkeypatch.setattr(PageService, "get_history", AsyncMock(return_value=[
        {"id": 207, "revision_number": 100, "author": "alice", "updated_at": timestamp},
        {"id": 206, "revision_number": 99, "author": "alice", "updated_at": timestamp},
    ]))

    versions = await _fetch_versions_for_history("Article", "main", limit=2)
    entries = _build_version_entries(versions)

    assert [entry["display_number"] for entry in entries] == [101, 100]
    assert [entry["revision_id"] for entry in entries] == [None, 207]
    assert [entry["is_current"] for entry in entries] == [True, False]


@pytest.mark.parametrize("branch", ["main", "draft & review"])
def test_history_widget_url_keeps_stable_revision_identity(branch):
    entry = {
        "index": 1, "revision_id": 207, "display_number": 100,
        "author": "alice", "updated_at_display": "2025-01-01 00:00:00", "is_current": False,
    }

    result = _format_version_entry("Space Ship", branch, entry)
    parsed = urlsplit(result["view_url"])
    query = parse_qs(parsed.query)

    assert parsed.path == "/history/Space%20Ship/1"
    assert query["revision_id"] == ["207"]
    if branch != "main":
        assert query["branch"] == [branch]
