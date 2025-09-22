"""
Export routes for WikiWare.
Provides endpoints to download database collections (API).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...middleware.auth_middleware import AuthMiddleware
from ...services.export_service import (
    ExportRateLimitError,
    ExportService,
    ExportUnavailableError,
)

router = APIRouter()


@router.get("/exports/collections")
async def download_collections(request: Request):
    """Allow an authenticated user to download wiki collections as a ZIP file."""
    user = await AuthMiddleware.require_auth(request)
    username = user["username"]
    filename = ExportService.build_export_filename()
    archive_stream = ExportService.generate_export_archive(username, filename=filename)

    try:
        first_chunk = await archive_stream.__anext__()
    except ExportRateLimitError as rate_error:
        next_allowed = rate_error.next_allowed
        logger = None  # Import logger if needed
        from loguru import logger
        logger.info(
            f"User {username} attempted export before cooldown expired; next allowed at {next_allowed.isoformat()}"
        )
        now = datetime.now(next_allowed.tzinfo or timezone.utc)
        retry_after = max(0, int((next_allowed - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail="You can only export collections once every 24 hours",
            headers={"Retry-After": str(retry_after)},
        ) from rate_error
    except ExportUnavailableError as unavailable_error:
        from loguru import logger
        logger.error(f"Export unavailable for user {username}: {unavailable_error}")
        raise HTTPException(
            status_code=503,
            detail="Collection export is temporarily unavailable",
        ) from unavailable_error
    except ValueError as missing_user:
        from loguru import logger
        logger.warning(f"Export denied for {username}: {missing_user}")
        raise HTTPException(status_code=404, detail="Account not found") from missing_user
    except StopAsyncIteration:
        async def empty_stream():
            if False:
                yield b""
        stream = empty_stream()
    else:
        async def stream_with_first_chunk(initial_chunk: bytes, remainder):
            yield initial_chunk
            async for chunk in remainder:
                yield chunk

        stream = stream_with_first_chunk(first_chunk, archive_stream)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(stream, media_type="application/zip", headers=headers)
