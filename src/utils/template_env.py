"""Utility helpers for shared Jinja2 templates."""

from fastapi.templating import Jinja2Templates

from .. import config as app_config
from ..config import TEMPLATE_DIR

_templates = Jinja2Templates(directory=TEMPLATE_DIR)
_templates.env.globals.setdefault("config", app_config)
_templates.env.globals.setdefault("APP_NAME", app_config.NAME)

def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance with global config."""
    return _templates
