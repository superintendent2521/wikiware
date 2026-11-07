"""Import real legacy-shaped documents into an isolated PostgreSQL schema."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

import migrate_to_postgres as importer
from src.schema import (
    branch_editors,
    favorites,
    image_hashes,
    metadata,
    page_branches,
    pages,
    revisions,
    sessions,
    settings,
    user_page_edit_counts,
    users,
)


class LegacyCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self):
        return iter(deepcopy(self.documents))


class LegacyClient:
    def __init__(self, documents):
        names = (
            "users", "pages", "branches", "history", "sessions", "image_hashes",
            "analytics_events", "system_logs", "settings",
        )
        self.database = SimpleNamespace(**{
            name: LegacyCollection(documents.get(name, [])) for name in names
        })
        self.closed = False

    def __getitem__(self, name):
        return self.database

    def close(self):
        self.closed = True


def configure_source(monkeypatch, postgres, documents):
    client = LegacyClient(documents)
    monkeypatch.setattr(importer, "MongoClient", lambda url: client)
    monkeypatch.setattr(importer, "create_async_engine", lambda *args, **kwargs: postgres)
    return client


@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_preserves_content_history_permissions_and_relationships(postgres, monkeypatch):
    now = datetime(2025, 1, 1, tzinfo=UTC)
    user_id = ObjectId()
    client = configure_source(monkeypatch, postgres, {
        "users": [{
            "_id": user_id, "username": "alice", "password_hash": "existing-hash",
            "total_edits": 3, "page_edits": {"Article": 3},
            "favorites": ["Article", {"title": "Article", "branch": "main"}],
            "created_at": now,
        }],
        "pages": [
            {"title": "Article", "content": "# Current\n\n[[Other:draft]]", "author": "alice",
             "branch": "main", "edit_permission": "select_users", "allowed_users": "alice",
             "updated_at": now + timedelta(days=2)},
            {"title": "Article", "content": "Draft content", "branch": "draft"},
        ],
        "branches": [{"page_title": "Article", "branch_name": "draft", "created_from": "release"}],
        # Deliberately supply newest first to test chronological revision numbering.
        "history": [
            {"title": "Article", "branch": "main", "content": "Second", "author": "alice",
             "updated_at": now + timedelta(days=1), "edit_summary": "Second summary"},
            {"title": "Article", "branch": "main", "content": "First", "author": "alice",
             "updated_at": now, "edit_summary": "First summary"},
        ],
        "sessions": [{"session_id": "legacy-session", "user_id": str(user_id),
                      "expires_at": now + timedelta(days=3650), "created_at": now}],
        "image_hashes": [{"filename": "image.png", "sha256": "a" * 64,
                          "size": 123, "url": "/media/uploads/image.png", "last_modified": now}],
        "settings": [{"_id": "feature_flags", "page_editing_enabled": False}],
    })

    await importer.migrate()

    async with postgres.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(pages)) == 1
        assert await connection.scalar(select(func.count()).select_from(page_branches)) == 2
        imported_user = (await connection.execute(select(users))).mappings().one()
        assert imported_user["password_hash"] == "existing-hash"
        assert imported_user["total_edits"] == 3
        imported_page = (await connection.execute(
            select(page_branches).where(page_branches.c.name == "main")
        )).mappings().one()
        assert imported_page["content"] == "# Current\n\n[[Other:draft]]"
        assert imported_page["edit_permission"] == "select_users"
        assert await connection.scalar(select(page_branches.c.created_from).where(
            page_branches.c.name == "draft"
        )) == "release"
        history = (await connection.execute(
            select(revisions.c.revision_number, revisions.c.content, revisions.c.edit_summary)
            .order_by(revisions.c.revision_number)
        )).all()
        assert history == [(1, "First", "First summary"), (2, "Second", "Second summary")]
        assert await connection.scalar(select(func.count()).select_from(branch_editors)) == 1
        assert await connection.scalar(select(func.count()).select_from(favorites)) == 1
        assert await connection.scalar(select(user_page_edit_counts.c.edit_count)) == 3
        assert await connection.scalar(select(sessions.c.user_id)) == imported_user["id"]
        assert await connection.scalar(select(image_hashes.c.url)) == "/media/uploads/image.png"
        assert await connection.scalar(select(settings.c.value)) == {"page_editing_enabled": False}
    assert client.closed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_refuses_a_nonempty_target_without_changing_it(postgres, monkeypatch):
    async with postgres.begin() as connection:
        await connection.execute(insert(users).values(username="existing", password_hash="hash"))
    configure_source(monkeypatch, postgres, {
        "users": [{"username": "new", "password_hash": "hash"}],
    })

    with pytest.raises(RuntimeError, match="not empty"):
        await importer.migrate()

    async with postgres.connect() as connection:
        assert (await connection.execute(select(users.c.username))).scalars().all() == ["existing"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_rolls_back_all_tables_when_a_later_document_fails(postgres, monkeypatch):
    configure_source(monkeypatch, postgres, {
        "users": [{"username": "alice", "password_hash": "hash"}],
        "pages": [
            {"title": "Duplicate", "branch": "main", "content": "One"},
            {"title": "Duplicate", "branch": "main", "content": "Two"},
        ],
    })

    with pytest.raises(IntegrityError):
        await importer.migrate()

    async with postgres.connect() as connection:
        for table in metadata.sorted_tables:
            assert await connection.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.parametrize("value", [
    "2025-01-01T00:00:00Z", "2025-01-01T01:00:00+01:00",
    datetime(2025, 1, 1), datetime(2025, 1, 1, tzinfo=UTC), 1735689600,
])
def test_legacy_timestamp_formats_normalize_to_utc(value):
    assert importer._utc(value) == datetime(2025, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, "invalid", {}, []])
def test_invalid_legacy_timestamps_are_absent(value):
    assert importer._utc(value) is None


def test_nested_legacy_metadata_becomes_json_serializable():
    document_id = ObjectId()
    converted = importer._json_value({
        "id": document_id,
        "nested": [{"timestamp": datetime(2025, 1, 1)}],
    })
    assert converted == {
        "id": str(document_id),
        "nested": [{"timestamp": "2025-01-01T00:00:00+00:00"}],
    }
