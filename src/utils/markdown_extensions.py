"""
Custom Markdown extensions for WikiWare.
Adds support for [[Page Title]] internal linking syntax and table rendering with color support.
"""

from urllib.parse import quote
import html as _html
from xml.etree.ElementTree import Element
from datetime import datetime, timezone
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString
from markdown.treeprocessors import Treeprocessor
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
        # Register the built-in tables extension (and expose reset hooks)
        table_extension = TableExtension()
        table_extension.extendMarkdown(md)
        md.registerExtension(table_extension)

        # Register custom inline pattern for color tags
        # Set priority to 165 to run before table extension (default 180)
        # Expanded to include newly supported colors
        color_pattern = r"\{\{\s*global\.color\.(red|green|blue|purple|pink|orange|yellow|gray|cyan)\s*\}\}"  # pylint: disable=C0301
        md.inlinePatterns.register(
            ColorTagProcessor(color_pattern, md), "color_tag", 165
        )

        # Register custom inline pattern for unix timestamps
        # Set priority to 164 to run before color tags
        unix_pattern = r"\{\{\s*global\.unix(?::(\d*))?\s*\}\}"
        md.inlinePatterns.register(
            UnixTimestampProcessor(unix_pattern, md), "unix_timestamp", 164
        )


class ImageFigureProcessor(Treeprocessor):
    """Convert stand-alone images into <figure> structures with optional captions."""

    def run(self, root):
        for parent in root.iter():
            children = list(parent)
            for index, child in enumerate(children):
                if child.tag != "p":
                    continue
                if (child.text or "").strip():
                    continue
                element_children = list(child)
                if len(element_children) != 1:
                    continue
                node = element_children[0]
                if (node.tail or "").strip():
                    continue
                wrapper = None
                image = None
                if node.tag == "img":
                    image = node
                elif (
                    node.tag == "a"
                    and len(node) == 1
                    and node[0].tag == "img"
                    and not (node.text or "").strip()
                    and not (node[0].tail or "").strip()
                ):
                    wrapper = node
                    image = node[0]
                else:
                    continue
                if image is None or (image.tail or "").strip():
                    continue
                figure = Element("figure")
                figure.set("class", "wiki-image")
                if wrapper is not None:
                    wrapper.tail = ""
                    image.tail = ""
                    figure.append(wrapper)
                else:
                    image.tail = ""
                    figure.append(image)
                caption_text = (image.get("title") or "").strip()
                if "title" in image.attrib:
                    image.attrib.pop("title")
                if caption_text:
                    figcaption = Element("figcaption")
                    figcaption.text = caption_text
                    figure.append(figcaption)
                figure.tail = child.tail
                parent.insert(index, figure)
                parent.remove(child)


class ImageFigureExtension(Extension):
    """Register the image figure processor for Markdown conversion."""

    def extendMarkdown(self, md):
        md.treeprocessors.register(ImageFigureProcessor(md), "wiki_image_figure", 15)


class ColorTagProcessor(InlineProcessor):
    """Process {{ global.color.COLOR }} syntax and convert to CSS color class."""

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


class UnixTimestampProcessor(InlineProcessor):
    """Process {{ global.unix:TIMESTAMP }} and render formatted UTC time; requires an explicit timestamp."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)

    def handleMatch(self, m, data):
        timestamp_str = m.group(1) if m.group(1) is not None else None

        try:
            if not timestamp_str:
                raise ValueError("Timestamp required for {{ global.unix }}")

            timestamp = int(timestamp_str)
            dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            formatted_time = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

            span = Element("span")
            span.set("class", "unix-timestamp")
            span.set("title", f"Unix timestamp: {timestamp}")
            span.set("data-timestamp", str(timestamp))
            span.set("data-source", "provided")
            span.text = formatted_time
            return span, m.start(0), m.end(0)

        except (ValueError, OSError):
            span = Element("span")
            span.set("class", "unix-timestamp-error")
            span.set("data-source", "error")
            if timestamp_str:
                span.set("title", f"Invalid timestamp: {timestamp_str}")
                span.set("data-timestamp", timestamp_str)
            else:
                span.set("title", "Timestamp missing for {{ global.unix }}")
            span.text = "Invalid timestamp"
            return span, m.start(0), m.end(0)
