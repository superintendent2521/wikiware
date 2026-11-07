"""Search uses literal queries, useful title ranking, and deterministic paging."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from src.schema import page_branches, pages
from src.services.page_service import PageService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed_search_pages(engine, documents):
    async with engine.begin() as connection:
        for document in documents:
            page_id = await connection.scalar(insert(pages).values(
                title=document["title"]
            ).returning(pages.c.id))
            await connection.execute(insert(page_branches).values(
                page_id=page_id, name=document.get("branch", "main"),
                content=document.get("content", ""),
                updated_at=document.get("updated_at", datetime(2025, 1, 1, tzinfo=UTC)),
            ))


@pytest.mark.parametrize("query,expected", [
    ("%", {"100% Ready", "Rates"}),
    ("_", {"engine_core", "Reference"}),
    ("!", {"Bang! Schedule"}),
])
async def test_like_wildcards_and_escape_character_are_literal(postgres, query, expected):
    await seed_search_pages(postgres, [
        {"title": "100% Ready"}, {"title": "100X Ready"},
        {"title": "Rates", "content": "Fuel uses 25%"},
        {"title": "engine_core"}, {"title": "engineXcore"},
        {"title": "Reference", "content": "engine_core is a field"},
        {"title": "Bang! Schedule"}, {"title": "BangX Schedule"},
    ])

    results = await PageService.search_pages(query)

    assert {row["title"] for row in results} == expected


async def test_exact_and_prefix_titles_rank_ahead_of_newer_body_matches(postgres):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    await seed_search_pages(postgres, [
        {"title": "Ship", "updated_at": start},
        {"title": "Ship systems", "updated_at": start + timedelta(days=1)},
        {"title": "Alpha manual", "content": "The ship manual", "updated_at": start + timedelta(days=2)},
        {"title": "Maintenance", "content": "Repair the ship", "updated_at": start + timedelta(days=3)},
    ])

    results = await PageService.search_pages("sHiP")

    assert [row["title"] for row in results] == ["Ship", "Ship systems", "Maintenance", "Alpha manual"]


@pytest.mark.parametrize("operation", ["search", "list"])
async def test_pagination_has_no_duplicates_or_gaps_when_timestamps_tie(postgres, operation):
    await seed_search_pages(postgres, [
        {"title": f"Page {index:02d}", "content": "shared query"}
        for index in (5, 1, 4, 0, 3, 2)
    ])

    async def fetch(offset):
        if operation == "search":
            return await PageService.search_pages("shared query", limit=2, offset=offset)
        return await PageService.get_pages_by_branch(limit=2, offset=offset)

    results = []
    for offset in (0, 2, 4):
        results.extend(row["title"] for row in await fetch(offset))

    assert results == [f"Page {index:02d}" for index in range(6)]
    assert await fetch(6) == []


async def test_search_results_remain_scoped_to_requested_branch(postgres):
    await seed_search_pages(postgres, [
        {"title": "Main article", "content": "needle", "branch": "main"},
        {"title": "Draft article", "content": "needle", "branch": "draft"},
    ])
    assert [row["title"] for row in await PageService.search_pages("needle", "main")] == ["Main article"]
    assert [row["title"] for row in await PageService.search_pages("needle", "draft")] == ["Draft article"]
