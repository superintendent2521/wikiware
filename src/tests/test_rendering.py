"""Compatibility fixtures for the single server-side Markdown dialect."""

from unittest.mock import AsyncMock

import pytest

from src.services import render_service
from src.services.render_service import render_markdown, render_sources_html

pytestmark = pytest.mark.asyncio


async def test_plain_markdown_does_not_query_wiki_statistics(monkeypatch):
    stats = AsyncMock()
    monkeypatch.setattr(render_service, "get_public_stats", stats)

    rendered, toc, sources = await render_markdown("# Heading\n\nA **bold** paragraph.")

    assert "<strong>bold</strong>" in rendered
    assert toc == []
    assert sources == []
    stats.assert_not_awaited()


async def test_wiki_links_encode_titles_and_branch_parameters():
    rendered, _, _ = await render_markdown("[[Space Ship]] and [[Space Ship:draft & review]]")
    assert 'href="/page/Space%20Ship"' in rendered
    assert 'href="/page/Space%20Ship?branch=draft%20%26%20review"' in rendered


async def test_code_samples_preserve_wiki_syntax_literally():
    content = "`[[Inline]]`\n\n```text\n[[Fenced:draft]]\n{{source|url=https://example.test}}\n```"
    rendered, _, sources = await render_markdown(content)
    assert "[[Inline]]" in rendered
    assert "[[Fenced:draft]]" in rendered
    assert "/page/Inline" not in rendered
    assert "/page/Fenced" not in rendered
    assert sources == []


async def test_nested_toc_points_to_sanitized_heading_ids():
    rendered, toc, _ = await render_markdown("# Overview\n\n## Detail\n\nText\n\n# Next")
    assert [item["id"] for item in toc] == ["overview", "next"]
    assert toc[0]["children"][0]["id"] == "detail"
    for anchor in ("overview", "detail", "next"):
        assert f'id="{anchor}"' in rendered


async def test_tables_and_image_caption_width_survive_rendering():
    content = '| Name | State |\n| --- | --- |\n| Ship | Ready |\n\n![Ship](/static/ship.png "Launch pad") {width=320}'
    rendered, _, _ = await render_markdown(content)
    assert "<table>" in rendered
    assert "<td>Ready</td>" in rendered
    assert '<figure class="wiki-image"' in rendered
    assert 'width="320"' in rendered
    assert "<figcaption>Launch pad</figcaption>" in rendered


async def test_citations_deduplicate_and_resolve_forward_references():
    content = (
        '[1]\n\n{{source|url=https://example.test/source|title=Flight report|author=Alice}}'
        '\n\n{{source|url=https://example.test/source|title=Same source}}'
    )
    rendered, _, sources = await render_markdown(content)
    assert sources == [{"id": 1, "url": "https://example.test/source", "title": "Flight report", "author": "Alice"}]
    assert rendered.count('href="#source-1"') == 3
    assert 'id="source-1"' in render_sources_html(sources)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,unsafe", "file:///private", "//example.test/source",
])
async def test_unsafe_citation_urls_never_enter_source_metadata(url):
    rendered, _, sources = await render_markdown("{{source|url=" + url + "}}")
    assert sources == []
    assert "Invalid source" in rendered


async def test_scripts_event_handlers_and_unsafe_links_are_removed():
    content = '<script>alert(1)</script><img src="/safe.png" onerror="alert(2)"> [bad](javascript:alert(3))'
    rendered, _, _ = await render_markdown(content)
    assert "<script" not in rendered
    assert "onerror" not in rendered
    assert 'href="javascript:' not in rendered
    assert 'src="/safe.png"' in rendered


async def test_statistics_placeholders_preserve_zero_values(monkeypatch):
    stats = AsyncMock(return_value={"total_edits": 0, "total_pages": 2})
    monkeypatch.setattr(render_service, "get_public_stats", stats)
    rendered, _, _ = await render_markdown("Edits: {{ global.edits }}; pages: {{ global.pages }}")
    assert "Edits: 0; pages: 2" in rendered
    stats.assert_awaited_once()


async def test_sources_html_escapes_titles_and_urls():
    rendered = render_sources_html([{
        "id": 1, "url": "https://example.test/?a=1&b=2", "title": '<img src=x onerror="bad">',
    }])
    assert "a=1&amp;b=2" in rendered
    assert "&lt;img" in rendered
    assert '<img src=x' not in rendered


async def test_legacy_timestamp_and_color_markers_remain_supported():
    rendered, _, _ = await render_markdown(
        "{{ global.unix:1700000000 }}\n\n{{ global.color.red }}"
    )
    assert "2023-11-14 22:13:20 UTC" in rendered
    assert '<span class="color-red"></span>' in rendered
