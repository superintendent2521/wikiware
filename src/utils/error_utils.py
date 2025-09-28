from fastapi import Request
from fastapi.responses import HTMLResponse
from ..utils.template_env import get_templates

def render_error_page(
    request: Request,
    user: dict | None = None,
    title: str = "Error",
    message: str = "An error occurred",
    status_code: int = 403,
    branch: str = "main",
    offline: bool = False,
    csrf_token: str = "",
) -> HTMLResponse:
    """Render a standardized error template for page operations."""
    templates = get_templates()
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": title,
            "message": message,
            "user": user,
            "branch": branch,
            "offline": offline,
            "csrf_token": csrf_token,
        },
        status_code=status_code,
    )
