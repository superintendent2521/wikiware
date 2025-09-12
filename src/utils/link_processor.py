"""
Helper module for processing internal links in page content.
Provides a function to convert [[Page Title]] and [[Page:Branch]] syntax to HTML links.
"""

import re
from typing import Optional

def process_internal_links(content: str) -> str:
    """
    Process internal links in page content.
    Converts [[Page Title]] to <a href="/page/Page%20Title">Page Title</a>
    Converts [[Page:Branch]] to <a href="/page/Page?branch=Branch">Page</a>
    
    Args:
        content: Raw page content with potential [[...]] links
        
    Returns:
        Content with internal links converted to HTML anchors
    """
    if not content:
        return content
        
    # Pattern to match [[Page Title]] and [[Page:Branch]]
    # Uses non-greedy matching to handle multiple links in one line
    pattern = r'\[\[([^\]]+?)\]\]'
    
    def replace_link(match):
        full_match = match.group(1).strip()
        
        if ':' in full_match:
            parts = full_match.split(':', 1)
            title = parts[0].strip()
            branch = parts[1].strip()
            # URL-encode both title and branch
            encoded_title = title.replace(' ', '%20')
            encoded_branch = branch.replace(' ', '%20')
            return f'<a href="/page/{encoded_title}?branch={encoded_branch}">{title}</a>'
        else:
            # Default to main branch if no branch specified
            title = full_match
            encoded_title = title.replace(' ', '%20')
            return f'<a href="/page/{encoded_title}">{title}</a>'
    
    # Replace all matches
    result = re.sub(pattern, replace_link, content)
    return result
