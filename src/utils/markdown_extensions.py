"""
Custom Markdown extensions for WikiWare.
Adds support for [[Page Title]] internal linking syntax and table rendering with color support.
"""
from urllib.parse import quote
import html as _html
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString
from markdown.extensions.tables import TableExtension


class InternalLinkProcessor(InlineProcessor):
    """Process [[Page Title]] and [[Page:Branch]] syntax and convert to internal links."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)

    def handleMatch(self, m, data):
        # Extract the full match from [[...]]
        full_match = m.group(1).strip()

        # Check if it contains a colon for branch specification
        if ":" in full_match:
            parts = full_match.split(":", 1)
            title = parts[0].strip()
            branch = parts[1].strip()
            encoded_title = quote(title, safe="")
            encoded_branch = quote(branch, safe="")
            link_text = _html.escape(title)
            link = f'<a href="/page/{encoded_title}?branch={encoded_branch}">{link_text}</a>'
        else:
            # Default to main branch if no branch specified
            title = full_match
            encoded_title = quote(title, safe="")
            link_text = _html.escape(title)
            link = f'<a href="/page/{encoded_title}">{link_text}</a>'

        logger = __import__("loguru").logger
        logger.debug(f"Internal link processed: {full_match} -> {link}")
        return AtomicString(link), m.start(0), m.end(0)


class InternalLinkExtension(Extension):
    """Markdown extension to support [[Page Title]] internal links."""

    def extendMarkdown(self, md):
        # Pattern to match [[Page Title]] and [[Page:Branch]]
        # More explicit pattern to ensure it matches correctly
        pattern = r"\[\[([^\]]+?)\]\]"
        # Use a higher priority (lower number) to ensure it runs before other inline patterns
        md.inlinePatterns.register(
            InternalLinkProcessor(pattern, md), "internal_link", 170
        )


# Add table extension to support GitHub-flavored tables with color support
class TableExtensionWrapper(Extension):
    """Wrapper to add table extension with default settings and color tagging support."""

    def extendMarkdown(self, md):
        # Register the built-in tables extension
        md.registerExtension(TableExtension())

        # Register custom inline pattern for color tags
        # Set priority to 165 to run before table extension (default 180)
        # Expanded to include newly supported colors
        color_pattern = r"\{\{\s*global\.color\.(red|green|blue|purple|pink|orange|yellow|gray|cyan)\s*\}\}" # pylint: disable=C0301
        md.inlinePatterns.register(
            ColorTagProcessor(color_pattern, md), "color_tag", 165
        )


class ColorTagProcessor(InlineProcessor):
    """Process {{ global.color.pink }} syntax and convert to CSS color class."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)

    def handleMatch(self, m, data):
        color_name = m.group(1).strip()
        # Map color names to CSS classes
        color_classes = {
            "red": "color-red",
            "green": "color-green",
            "blue": "color-blue",
            "purple": "color-purple",
            "pink": "color-pink",
            "orange": "color-orange",
            "yellow": "color-yellow",
            "gray": "color-gray",
            "cyan": "color-cyan",
        }
        css_class = color_classes.get(color_name, "")
        # Return only the span, and consume the entire match so the raw code doesn't appear
        return AtomicString(f'<span class="{css_class}"></span>'), m.start(0), m.end(0)




