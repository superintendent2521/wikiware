"""
HTML sanitizer for user-provided content rendered from Markdown.
Uses Bleach to remove dangerous tags/attributes and prevent XSS.
"""

from typing import Iterable
import bleach


# Allow a conservative set of HTML tags typically produced by Markdown
ALLOWED_TAGS: Iterable[str] = {
    "a",
    "sup",
    "abbr",
    "acronym",
    "b",
    "blockquote",
    "code",
    "em",
    "i",
    "li",
    "ol",
    "strong",
    "ul",
    "p",
    "pre",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "img",
    "figure",
    "figcaption",
}

ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel", "class"],
    "img": ["src", "alt", "title", "width", "height"],
    "figure": ["class"],
    "figcaption": ["class"],
    "span": ["class", "title", "data-timestamp", "data-source"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}


for heading in ("h1", "h2", "h3", "h4", "h5", "h6"):
    ALLOWED_ATTRIBUTES[heading] = ["id"]

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(html: str) -> str:
    """Sanitize HTML produced from Markdown to prevent XSS."""
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
