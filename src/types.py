"""
Shared typing helpers for WikiWare.

These helpers keep Basedpyright satisfied when we attach dynamic attributes
to ``request.state`` or pass around lightweight user payloads.
"""

from __future__ import annotations

from typing import Protocol, TypedDict, cast

from fastapi import Request

from .services.settings_service import Banner, FeatureFlags


class WikiRequestState(Protocol):
    """Shape of attributes we stash on ``request.state``."""

    feature_flags: FeatureFlags
    global_banner: Banner
    csp_nonce: str


class UserContext(TypedDict, total=False):
    """Minimal user payload used throughout routes."""

    username: str
    is_admin: bool
    is_active: bool


def get_state(request: Request) -> WikiRequestState:
    """Return ``request.state`` with a typed view for static checkers."""
    return cast(WikiRequestState, request.state)


def ensure_feature_flags(request: Request, feature_flags: FeatureFlags) -> FeatureFlags:
    """Attach feature flags to the request state with proper typing."""
    state = cast(WikiRequestState, request.state)
    state.feature_flags = feature_flags
    return feature_flags


def set_banner(request: Request, banner: Banner) -> None:
    """Attach the global banner to the request state with proper typing."""
    state = cast(WikiRequestState, request.state)
    state.global_banner = banner


def set_csp_nonce(request: Request, nonce: str) -> str:
    """Attach the CSP nonce to the request state with proper typing."""
    state = cast(WikiRequestState, request.state)
    state.csp_nonce = nonce
    return nonce
