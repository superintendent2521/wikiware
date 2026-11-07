"""Utility helpers for shared Jinja2 templates."""

from fastapi.templating import Jinja2Templates

from .. import config as app_config
from ..config import TEMPLATE_DIR

class WikiWareTemplates(Jinja2Templates):
    """Keep legacy route calls working with current Starlette templates."""

    def TemplateResponse(
        self,
        request_or_name,
        name_or_context=None,
        context=None,
        status_code=200,
        headers=None,
        media_type=None,
        background=None,
    ):
        if isinstance(request_or_name, str):
            name = request_or_name
            context = name_or_context or context or {}
            request = context["request"]
        else:
            request = request_or_name
            name = name_or_context

        return super().TemplateResponse(
            request,
            name,
            context=context,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )


_templates = WikiWareTemplates(directory=TEMPLATE_DIR)
_templates.env.globals.setdefault("config", app_config)
_templates.env.globals.setdefault("APP_NAME", app_config.NAME)


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance with global config."""
    return _templates
