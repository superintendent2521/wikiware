"""
Unit tests for validation and navigation utilities.

These tests avoid HTTP calls and exercise pure functions so they can run in CI.
"""

import json
from urllib.parse import urlencode

import pytest

from src.utils import navigation_history, validation


class FakeURL:
    """Lightweight URL helper mirroring Starlette's include_query_params."""

    def __init__(self, base: str):
        self.base = base

    def include_query_params(self, **kwargs) -> str:
        query = urlencode(kwargs)
        return f"{self.base}?{query}" if query else self.base


class FakeRequest:
    """Minimal request stub exposing cookies, query_params, and url_for."""

    def __init__(self, cookies=None, query_params=None):
        self.cookies = cookies or {}
        self.query_params = query_params or {}

    def url_for(self, name: str, **kwargs) -> FakeURL:
        if name == "home":
            return FakeURL("/")
        if name == "get_page":
            return FakeURL(f"/page/{kwargs.get('title', '')}")
        raise KeyError(f"Unhandled route {name}")


def test_is_valid_title_accepts_simple_titles():
    assert validation.is_valid_title("Hello World-123")
    assert not validation.is_valid_title("")
    assert not validation.is_valid_title("../etc/passwd")
    assert not validation.is_valid_title("/absolute")
    assert not validation.is_valid_title("snake:case")


def test_is_valid_branch_name_rejects_reserved_and_paths():
    assert validation.is_valid_branch_name("feature_branch")
    assert not validation.is_valid_branch_name("main")
    assert not validation.is_valid_branch_name("foo/bar")
    assert not validation.is_valid_branch_name("")


def test_is_safe_branch_parameter_matches_pattern():
    assert validation.is_safe_branch_parameter("draft-1")
    assert validation.is_safe_branch_parameter("main")
    assert not validation.is_safe_branch_parameter("bad branch")
    assert not validation.is_safe_branch_parameter(None)


def test_sanitize_redirect_path_blocks_external_targets():
    assert validation.sanitize_redirect_path("https://evil.example.com/foo") == "/"
    assert validation.sanitize_redirect_path("//double/slash") == "/"
    assert validation.sanitize_redirect_path("../escape") == "/"
    assert validation.sanitize_redirect_path("/safe/path?x=1") == "/safe/path?x=1"
    assert validation.sanitize_redirect_path(None, default="/default") == "/default"


def test_sanitize_filename_strips_dangerous_characters():
    assert validation.sanitize_filename("nice.png") == "nice.png"
    assert validation.sanitize_filename("../weird:name?.png") == ".._weird_name_.png"


def test_load_history_cookie_filters_invalid_entries():
    valid_entry = {"title": "Page One", "branch": "draft", "is_home": False}
    mixed_cookie = json.dumps(
        [
            valid_entry,
            {"title": ""},  # invalid
            "string",  # invalid type
            {"title": "Page Two", "branch": 123},  # invalid branch type
        ]
    )
    request = FakeRequest(cookies={navigation_history.HISTORY_COOKIE_NAME: mixed_cookie})
    history = navigation_history.load_history_cookie(request)
    assert history == [
        {"title": "Page One", "branch": "draft", "is_home": False},
        {"title": "Page Two", "branch": "main", "is_home": False},
    ]


def test_apply_history_update_deduplicates_and_caps_length():
    base_entry = navigation_history.build_history_entry("Home", "main", True)
    history = [navigation_history.build_history_entry(f"P{i}", "main", False) for i in range(25)]
    updated = navigation_history.apply_history_update(history, base_entry, is_back_navigation=False)
    assert updated[-1] == base_entry
    assert len(updated) == navigation_history.HISTORY_MAX_LENGTH
    # Should not keep duplicates when adding the same entry again
    updated_again = navigation_history.apply_history_update(updated, base_entry, is_back_navigation=False)
    assert updated_again.count(base_entry) == 1


def test_apply_history_update_handles_back_navigation():
    a = navigation_history.build_history_entry("A", "main", False)
    b = navigation_history.build_history_entry("B", "main", False)
    c = navigation_history.build_history_entry("C", "main", False)
    d = navigation_history.build_history_entry("D", "main", False)
    history = [a, b, c]
    back_history = navigation_history.apply_history_update(history, b, is_back_navigation=True)
    assert back_history == [a, b]
    # If target not found, it should append
    back_history_missing = navigation_history.apply_history_update(history, d, is_back_navigation=True)
    assert back_history_missing[-1] == d
    assert d in back_history_missing


def test_resolve_previous_entry_skips_same_page_branch():
    a = navigation_history.build_history_entry("A", "main", False)
    b = navigation_history.build_history_entry("B", "main", False)
    c = navigation_history.build_history_entry("A", "main", False)
    history = [a, b, c]
    previous = navigation_history.resolve_previous_entry(history, c)
    assert previous == b
    assert navigation_history.resolve_previous_entry([a], a) is None


def test_build_history_link_includes_back_flag_and_branch():
    entry = navigation_history.build_history_entry("Doc", "draft", False)
    request = FakeRequest()
    url = navigation_history.build_history_link(request, entry)
    assert "/page/Doc?" in url
    assert navigation_history.HISTORY_QUERY_PARAM in url
    assert "branch=draft" in url


def test_prepare_navigation_context_returns_previous_context():
    entry = navigation_history.build_history_entry("Doc", "main", False)
    request = FakeRequest(
        cookies={
            navigation_history.HISTORY_COOKIE_NAME: json.dumps(
                [
                    navigation_history.build_history_entry("Home", "main", True),
                    entry,
                ]
            )
        }
    )
    history, previous = navigation_history.prepare_navigation_context(
        request,
        title="Doc",
        branch="main",
        is_home=False,
    )
    assert history[-1]["title"] == "Doc"
    assert previous is not None
    assert previous["title"] == "Home"
