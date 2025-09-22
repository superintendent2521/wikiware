"""
Export routes for WikiWare.
Provides endpoints to download database collections with per-user rate limiting.
"""

import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ..middleware.auth_middleware import AuthMiddleware
from ..services.export_service import (
    ExportRateLimitError,
    ExportService,
    ExportUnavailableError,
)
from ..utils.template_env import get_templates

router = APIRouter()
templates = get_templates()


@router.get("/exports", response_class=HTMLResponse)
async def export_collections_page(
    request: Request, csrf_protect: CsrfProtect = Depends()
):
    """Render the collections export confirmation page with rate-limit warning."""
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    template = templates.TemplateResponse(
        "exports.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "offline": False,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/exports/collections")
async def download_collections(request: Request):
    """Allow an authenticated user to download wiki collections as a ZIP file."""
    user = await AuthMiddleware.require_auth(request)
    username = user["username"]

    try:
        archive_bytes, filename = await ExportService.generate_export_archive(username)
    except ExportRateLimitError as rate_error:
        next_allowed = rate_error.next_allowed
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
        logger.error(f"Export unavailable for user {username}: {unavailable_error}")
        raise HTTPException(
            status_code=503,
            detail="Collection export is temporarily unavailable",
        ) from unavailable_error
    except ValueError as missing_user:
        logger.warning(f"Export denied for {username}: {missing_user}")
        raise HTTPException(status_code=404, detail="Account not found") from missing_user

    stream = io.BytesIO(archive_bytes)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(stream, media_type="application/zip", headers=headers)
