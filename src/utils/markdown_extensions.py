"""
Custom Markdown extensions for WikiWare.
Adds support for [[Page Title]] internal linking syntax.
"""

import re
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString


class InternalLinkProcessor(InlineProcessor):
    """Process [[Page Title]] and [[Page:Branch]] syntax and convert to internal links."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)

    def handleMatch(self, m, data):
        # Extract the full match from [[...]]
        full_match = m.group(1).strip()

        # Check if it contains a colon for branch specification
        if ':' in full_match:
            parts = full_match.split(':', 1)
            title = parts[0].strip()
            branch = parts[1].strip()
            # URL-encode both title and branch
            encoded_title = title.replace(' ', '%20')
            encoded_branch = branch.replace(' ', '%20')
            link = f'<a href="/page/{encoded_title}?branch={encoded_branch}">{title}</a>'
        else:
            # Default to main branch if no branch specified
            title = full_match
            encoded_title = title.replace(' ', '%20')
            link = f'<a href="/page/{encoded_title}">{title}</a>'

        logger = __import__('loguru').logger
        logger.debug(f"Internal link processed: {full_match} -> {link}")
        return AtomicString(link), m.start(0), m.end(0)


class InternalLinkExtension(Extension):
    """Markdown extension to support [[Page Title]] internal links."""

    def extendMarkdown(self, md):
        # Pattern to match [[Page Title]] and [[Page:Branch]]
        # More explicit pattern to ensure it matches correctly
        pattern = r'\[\[([^\]]+?)\]\]'
        # Use a higher priority (lower number) to ensure it runs before other inline patterns
        md.inlinePatterns.register(InternalLinkProcessor(pattern, md), 'internal_link', 170)
