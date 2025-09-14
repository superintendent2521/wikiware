"""
HTML sanitizer for user-provided content rendered from Markdown.
Uses Bleach to remove dangerous tags/attributes and prevent XSS.
"""

from typing import Iterable
import bleach


# Allow a conservative set of HTML tags typically produced by Markdown
ALLOWED_TAGS: Iterable[str] = {
    'a', 'abbr', 'acronym', 'b', 'blockquote', 'code', 'em', 'i', 'li', 'ol', 'strong', 'ul', 'p', 'pre', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'span', 'img'
}

ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'rel', 'target'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    'span': ['class'],
    'th': ['colspan', 'rowspan'],
    'td': ['colspan', 'rowspan'],
}

ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']


def sanitize_html(html: str) -> str:
    """Sanitize HTML produced from Markdown to prevent XSS."""
    # Ensure links get rel attributes for safety when target=_blank is used
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    cleaned = bleach.linkify(cleaned, callbacks=[bleach.linkifier.nofollow, bleach.linkifier.target_blank])
    return cleaned

