"""Responses shared by the normal and personal page editors."""

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from .template_env import get_templates


def save_succeeded(request: Request, redirect_url: str):
    """Acknowledge persistence before the browser clears its local recovery copy."""
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"redirect_url": redirect_url})
    return RedirectResponse(url=redirect_url, status_code=303)


def save_failed(request: Request, message: str, status_code: int, content: str):
    """Keep submitted content recoverable even without JavaScript or local storage."""
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"error": message}, status_code=status_code)
    return get_templates().TemplateResponse(
        "save_error.html",
        {"request": request, "message": message, "content": content},
        status_code=status_code,
    )
