"""Archives must be complete snapshots and reserve export cooldowns atomically."""

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from zipfile import ZipFile

import pytest
from sqlalchemy import func, insert, select, update

from src.schema import page_branches, pages, revisions, users
from src.services.export_service import ExportRateLimitError, ExportService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed_export(engine, *, previous_export=None):
    async with engine.begin() as connection:
        await connection.execute(insert(users).values(
            username="alice", password_hash="hash", last_collection_export_at=previous_export,
        ))
        page_id = await connection.scalar(insert(pages).values(title="Article").returning(pages.c.id))
        return await connection.scalar(insert(page_branches).values(
            page_id=page_id, name="main", content="Before", author_name="alice",
        ).returning(page_branches.c.id))


async def read_archive(username="alice"):
    content = b"".join([chunk async for chunk in ExportService.generate_export_archive(username)])
    with ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {name: json.loads(archive.read(name)) for name in archive.namelist()}


async def test_export_is_a_valid_archive_and_reserves_successful_cooldown(postgres):
    await seed_export(postgres)

    archive = await read_archive()

    assert set(archive) == {"pages.json", "history.json", "image.json", "branches.json"}
    assert archive["pages.json"][0]["content"] == "Before"
    assert archive["branches.json"][0]["content"] == "Before"
    assert archive["history.json"] == []
    async with postgres.connect() as connection:
        assert await connection.scalar(select(users.c.last_collection_export_at)) is not None
    with pytest.raises(ExportRateLimitError):
        await read_archive()


async def test_all_export_sources_share_one_repeatable_read_snapshot(postgres, monkeypatch):
    branch_id = await seed_export(postgres)
    original_stream = ExportService._stream_collection_json
    changed = False

    async def mutate_between_sources(cls, connection, statement, label):
        nonlocal changed
        async for chunk in original_stream(connection, statement, label):
            yield chunk
        if label == "pages":
            async with postgres.begin() as writer:
                await writer.execute(update(page_branches).where(page_branches.c.id == branch_id)
                                     .values(content="After"))
                await writer.execute(insert(revisions).values(
                    page_branch_id=branch_id, revision_number=1, content="Before",
                ))
            changed = True

    monkeypatch.setattr(ExportService, "_stream_collection_json", classmethod(mutate_between_sources))

    archive = await read_archive()

    assert changed
    assert archive["pages.json"][0]["content"] == "Before"
    assert archive["branches.json"][0]["content"] == "Before"
    assert archive["history.json"] == []
    async with postgres.connect() as connection:
        assert await connection.scalar(select(page_branches.c.content)) == "After"
        assert await connection.scalar(select(func.count()).select_from(revisions)) == 1


async def test_simultaneous_reservations_allow_only_one_export(postgres):
    await seed_export(postgres)

    results = await asyncio.gather(
        ExportService._reserve_export("alice"), ExportService._reserve_export("alice"),
        return_exceptions=True,
    )

    assert sum(isinstance(result, datetime) for result in results) == 1
    assert sum(isinstance(result, ExportRateLimitError) for result in results) == 1


@pytest.mark.parametrize("had_previous_export", [False, True])
async def test_interrupted_download_releases_its_reservation(postgres, had_previous_export):
    previous = datetime.now(UTC) - timedelta(days=2) if had_previous_export else None
    await seed_export(postgres, previous_export=previous)
    stream = ExportService.generate_export_archive("alice")
    try:
        assert await anext(stream)
        async with postgres.connect() as connection:
            assert await connection.scalar(select(users.c.last_collection_export_at)) != previous
    finally:
        await stream.aclose()

    async with postgres.connect() as connection:
        assert await connection.scalar(select(users.c.last_collection_export_at)) == previous


async def test_interrupted_download_cannot_release_a_newer_reservation(postgres):
    await seed_export(postgres)
    stream = ExportService.generate_export_archive("alice")
    newer = datetime.now(UTC) + timedelta(minutes=1)
    try:
        assert await anext(stream)
        async with postgres.begin() as connection:
            await connection.execute(update(users).values(last_collection_export_at=newer))
    finally:
        await stream.aclose()

    async with postgres.connect() as connection:
        assert await connection.scalar(select(users.c.last_collection_export_at)) == newer
