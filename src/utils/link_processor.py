"""
Helper module for processing internal links in page content.
Provides a function to convert [[Page Title]] and [[Page:Branch]] syntax to HTML links.
"""

import html as _html
from urllib.parse import quote
from .template_processor import render_template_content

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

    def build_link(link_body: str) -> str:
        full_match = link_body.strip()

        if ':' in full_match:
            parts = full_match.split(':', 1)
            title = parts[0].strip()
            branch = parts[1].strip()
            encoded_title = quote(title, safe='')
            encoded_branch = quote(branch, safe='')
            safe_text = _html.escape(title)
            return f'<a href="/page/{encoded_title}?branch={encoded_branch}">{safe_text}</a>'
        else:
            title = full_match
            encoded_title = quote(title, safe='')
            safe_text = _html.escape(title)
            return f'<a href="/page/{encoded_title}">{safe_text}</a>'

    # Manual parser avoids regex backtracking DoS on crafted input
    pieces = []
    index = 0
    content_length = len(content)

    while index < content_length:
        start = content.find('[[', index)
        if start == -1:
            pieces.append(content[index:])
            break

        pieces.append(content[index:start])
        end = content.find(']]', start + 2)
        if end == -1:
            pieces.append(content[start:])
            break

        link_text = content[start + 2:end]
        pieces.append(build_link(link_text))
        index = end + 2
    else:
        pieces.append(content[index:])

    if not pieces:
        return content

    result = ''.join(pieces)
    return result
