"""
Simple in-memory rate limiting helpers for WikiWare.
Used to throttle high-risk endpoints like login and editing.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from time import monotonic
from typing import Awaitable, Callable, Deque, Dict, Optional

from fastapi import HTTPException, Request

from .auth_middleware import AuthMiddleware

DEFAULT_REQUESTS_PER_WINDOW = 5
WINDOW_SECONDS = 60
DEFAULT_DETAIL = "Too many requests. Please try again later."


class RateLimiter:
    """Track request timestamps and enforce per-key rate limits."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._records: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = monotonic()

    def _cleanup_if_needed(self, now: float) -> None:
        """
        Remove stale keys whose latest request is outside the active window.

        This runs at most once per window to keep the check path cheap.
        """
        if now - self._last_cleanup < self._window_seconds:
            return

        stale_keys = [
            key
            for key, timestamps in self._records.items()
            if not timestamps or now - timestamps[-1] >= self._window_seconds
        ]
        for key in stale_keys:
            self._records.pop(key, None)
        self._last_cleanup = now

    async def check(self, key: str, *, detail: Optional[str] = None) -> None:
        """Raise HTTP 429 if the caller exceeded the allowed request budget."""
        now = monotonic()
        async with self._lock:
            self._cleanup_if_needed(now)
            timestamps = self._records.setdefault(key, deque())
            # Drop timestamps outside the sliding window
            while timestamps and now - timestamps[0] >= self._window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self._max_requests:
                retry_after = timestamps[0] + self._window_seconds - now
                retry_after_seconds = max(1, math.ceil(retry_after))
                message = detail or DEFAULT_DETAIL
                raise HTTPException(
                    status_code=429,
                    detail=message,
                    headers={"Retry-After": str(retry_after_seconds)},
                )

            timestamps.append(now)


_rate_limiter = RateLimiter(DEFAULT_REQUESTS_PER_WINDOW, WINDOW_SECONDS)


def _client_identifier(request: Request) -> str:
    """Return a stable identifier for the caller based on IP headers."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidate = xff.split(",")[0].strip()
        if candidate:
            return candidate
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _build_rate_limit_key(
    request: Request, scope: str, use_user_identity: bool
) -> str:
    """Build a rate limit key using the username when available."""
    if use_user_identity:
        try:
            user = await AuthMiddleware.get_current_user(request)
        except Exception:
            user = None
        if user and user.get("username"):
            username = str(user["username"]).casefold()
            return f"{scope}:user:{username}"
    client_id = _client_identifier(request)
    return f"{scope}:ip:{client_id}"


def rate_limit(
    scope: str,
    *,
    detail: Optional[str] = None,
    use_user_identity: bool = False,
) -> Callable[[Request], Awaitable[None]]:
    """
    Return a dependency that enforces a rate limit for the given scope.

    Args:
        scope: Logical route identifier (e.g., "login" or "page-edit").
        detail: Optional custom error message for rate limit violations.
        use_user_identity: Prefer the authenticated username over IP when available.
    """

    async def dependency(request: Request) -> None:
        key = await _build_rate_limit_key(
            request, scope, use_user_identity=use_user_identity
        )
        await _rate_limiter.check(key, detail=detail)

    return dependency


__all__ = ["rate_limit"]
