"""Authenticated previews use exactly the same renderer as published pages."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

from ...middleware.auth_middleware import AuthMiddleware
from ...middleware.rate_limiter import rate_limit
from ...services.render_service import render_markdown, render_sources_html

router = APIRouter()


@router.post("/preview", dependencies=[Depends(rate_limit("preview", use_user_identity=True))])
async def preview_markdown(
    request: Request, content: str = Form(""), csrf_protect: CsrfProtect = Depends()
):
    try:
        await csrf_protect.validate_csrf(request)
    except CsrfProtectError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await AuthMiddleware.require_auth(request)
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Preview content is too large")
    rendered, toc, sources = await render_markdown(content)
    return {"html": rendered + render_sources_html(sources), "toc_items": toc, "sources": sources}
