"""
Helper module for processing internal links in page content.
Provides a function to convert [[Page Title]] and [[Page:Branch]] syntax to HTML links.
"""

import html as _html
from typing import Tuple
from urllib.parse import quote

from .template_processor import render_template_content


def parse_internal_link_target(link_body: str) -> Tuple[str, str | None]:
    """Return the title and optional branch from a [[...]] link body."""
    link_target = link_body.strip()
    if ":" not in link_target:
        return link_target, None

    title, branch = link_target.split(":", 1)
    return title.strip(), branch.strip()


def build_internal_link_html(link_body: str) -> str:
    """Build the HTML anchor for a wiki-style internal link body."""
    title, branch = parse_internal_link_target(link_body)
    encoded_title = quote(title, safe="")
    safe_text = _html.escape(title)

    if branch is None:
        return f'<a href="/page/{encoded_title}">{safe_text}</a>'

    encoded_branch = quote(branch, safe="")
    return f'<a href="/page/{encoded_title}?branch={encoded_branch}">{safe_text}</a>'


async def process_internal_links(content: str) -> str:
    """
    Process internal links and template variables in page content.
    Converts [[Page Title]] to <a href="/page/Page%20Title">Page Title</a>
    Converts [[Page:Branch]] to <a href="/page/Page?branch=Branch">Page</a>
    Also renders Jinja2 template variables like {{ global.edits }}

    Args:
        content: Raw page content with potential [[...]] links and {{ variables }}

    Returns:
        Content with internal links converted to HTML anchors and template variables rendered
    """
    if not content:
        return content

    # First, render any Jinja2 template variables (e.g., {{ global.edits }})
    content = await render_template_content(content)

    # Manual parser avoids regex backtracking DoS on crafted input
    pieces = []
    index = 0
    content_length = len(content)

    while index < content_length:
        start = content.find("[[", index)
        if start == -1:
            pieces.append(content[index:])
            break

        pieces.append(content[index:start])
        end = content.find("]]", start + 2)
        if end == -1:
            pieces.append(content[start:])
            break

        link_text = content[start + 2 : end]
        pieces.append(build_internal_link_html(link_text))
        index = end + 2

    return "".join(pieces)
