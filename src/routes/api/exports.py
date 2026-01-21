"""
Export API routes for WikiWare.
Provides endpoints to download database collections.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from ...middleware.auth_middleware import AuthMiddleware
from ...services.export_service import (
    ExportRateLimitError,
    ExportService,
    ExportUnavailableError,
)

EXPORT_MEDIA_TYPE = "application/zip"
EXPORT_PATH = "/exports/collections"
EXPORT_RETRY_MESSAGE = "You can only export collections once every 24 hours"
EXPORT_UNAVAILABLE_MESSAGE = "Collection export is temporarily unavailable"
EXPORT_NOT_FOUND_MESSAGE = "Account not found"

router = APIRouter()


async def _empty_stream() -> AsyncIterator[bytes]:
    if False:  # pragma: no cover - ensures this is treated as a generator
        yield b""


async def _stream_with_first_chunk(
    first_chunk: bytes, remainder: AsyncIterator[bytes]
) -> AsyncIterator[bytes]:
    yield first_chunk
    async for chunk in remainder:
        yield chunk


def _retry_after_seconds(next_allowed: datetime) -> int:
    current_time = datetime.now(next_allowed.tzinfo or timezone.utc)
    return max(0, int((next_allowed - current_time).total_seconds()))


async def _prepare_stream(archive_stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        first_chunk = await archive_stream.__anext__()
    except StopAsyncIteration:
        logger.info("Export stream is empty; sending zero-byte archive")
        return _empty_stream()
    return _stream_with_first_chunk(first_chunk, archive_stream)


@router.get(EXPORT_PATH)
async def download_collections(request: Request):
    """Allow an authenticated user to download wiki collections as a ZIP file."""
    user = await AuthMiddleware.require_auth(request)
    username = user["username"]
    filename = ExportService.build_export_filename()
    archive_stream = ExportService.generate_export_archive(username, filename=filename)

    try:
        stream = await _prepare_stream(archive_stream)
    except ExportRateLimitError as rate_error:
        next_allowed = rate_error.next_allowed
        logger.info(
            "User {} attempted export before cooldown expired; next allowed at {}",
            username,
            next_allowed.isoformat(),
        )
        retry_after = _retry_after_seconds(next_allowed)
        raise HTTPException(
            status_code=429,
            detail=EXPORT_RETRY_MESSAGE,
            headers={"Retry-After": str(retry_after)},
        ) from rate_error
    except ExportUnavailableError as unavailable_error:
        logger.error(
            "Collection export unavailable for user {}: {}", username, unavailable_error
        )
        raise HTTPException(
            status_code=503,
            detail=EXPORT_UNAVAILABLE_MESSAGE,
        ) from unavailable_error
    except ValueError as missing_user:
        logger.warning("Export denied for {}: {}", username, missing_user)
        raise HTTPException(
            status_code=404, detail=EXPORT_NOT_FOUND_MESSAGE
        ) from missing_user

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(stream, media_type=EXPORT_MEDIA_TYPE, headers=headers)
