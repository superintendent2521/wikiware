"""
Custom Markdown extensions for WikiWare.
Adds support for [[Page Title]] internal linking syntax and table rendering with color support.
"""

from urllib.parse import quote
import re
import html as _html
from xml.etree.ElementTree import Element
from datetime import datetime, timezone
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString
from markdown.treeprocessors import Treeprocessor
from markdown.extensions.tables import TableExtension


_SOURCE_PARAM_KEY_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _parse_source_params(params_str):
    """Split source parameters while tolerating pipes within values."""
    if not params_str:
        return {}

    segments = []
    buf = []
    length = len(params_str)
    index = 0

    while index < length:
        char = params_str[index]

        if char == '\\':
            index += 1
            if index < length:
                buf.append(params_str[index])
                index += 1
            else:
                buf.append('\\')
            continue

        if char == '|':
            remainder = params_str[index + 1:]
            if _SOURCE_PARAM_KEY_PATTERN.match(remainder):
                segments.append(''.join(buf))
                buf = []
                index += 1
                continue

        buf.append(char)
        index += 1

    segments.append(''.join(buf))

    params = {}
    for segment in segments:
        if not segment:
            continue
        if '=' not in segment:
            continue

        key, value = segment.split('=', 1)
        key = key.strip().lower()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in ('"', "'")):
            value = value[1:-1]

        params[key] = value

    return params


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


class SourceCollectorProcessor(InlineProcessor):
    """Process {{source|url=...|title=...|author=...}} and collect sources, replacing with citation."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)
        if not hasattr(md, 'sources'):
            md.sources = []
        if not hasattr(md, '_source_counter'):
            md._source_counter = 0
        if not hasattr(md, '_source_map'):
            md._source_map = {}  # url -> id for deduping

    def handleMatch(self, m, data):
        params_str = m.group(1).strip()
        params = _parse_source_params(params_str)

        url = params.get('url', '')
        title = params.get('title', url or 'Untitled Source')
        author = params.get('author', '')

        if not url:
            # Invalid, replace with error element so markup isn't escaped
            error_span = Element("span")
            error_span.set("class", "source-error")
            error_span.text = "[Invalid source: missing URL]"
            return error_span, m.start(0), m.end(0)

        # Dedupe by URL
        if url in self.md._source_map:
            source_id = self.md._source_map[url]
        else:
            self.md._source_counter += 1
            source_id = self.md._source_counter
            self.md._source_map[url] = source_id
            self.md.sources.append({
                'id': source_id,
                'url': url,
                'title': title,
                'author': author
            })

        # Replace with citation link element so the HTML renders server-side
        citation_sup = Element("sup")
        citation_link = Element("a")
        citation_link.set("href", f"#source-{source_id}")
        citation_link.set("class", "source-citation")
        citation_link.text = f"[ {source_id} ]"
        citation_sup.append(citation_link)
        return citation_sup, m.start(0), m.end(0)


class SourceCitationProcessor(InlineProcessor):
    """Process manual [1] citations and replace with links if valid source exists."""

    def __init__(self, pattern, md):
        super().__init__(pattern, md)

    def handleMatch(self, m, data):
        id_str = m.group(1).strip()
        try:
            source_id = int(id_str)
            citation_sup = Element("sup")
            citation_sup.set("data-source-id", str(source_id))
            citation_sup.text = f"[ {source_id} ]"
            return citation_sup, m.start(0), m.end(0)
        except ValueError:
            return AtomicString(m.group(0)), m.start(0), m.end(0)  # Keep as is


class SourceFinalizeTreeprocessor(Treeprocessor):
    """Finalize source citations after all inline processing has completed."""

    def run(self, root):
        sources = getattr(self.md, 'sources', [])
        sources_by_id = {str(source['id']): source for source in sources}

        for element in root.iter():
            if element.tag != "sup":
                continue

            source_id = element.attrib.pop("data-source-id", None)
            if source_id is None:
                continue

            # Remove any placeholder children/text before building the final markup
            element.text = ""
            for child in list(element):
                element.remove(child)

            source = sources_by_id.get(source_id)
            if source is not None:
                element.attrib.pop("class", None)
                citation_link = Element("a")
                citation_link.set("href", f"#source-{source_id}")
                citation_link.set("class", "source-citation")
                citation_link.text = f"[ {source_id} ]"
                element.append(citation_link)
            else:
                element.set("class", "source-invalid")
                element.text = f"[ {source_id} ]"

        return root


class SourceExtension(Extension):
    """Markdown extension to support source citations."""

    def extendMarkdown(self, md):
        # Source collector pattern: {{source|key=val|...}}
        source_pattern = r'\{\{source\|([^}]+)\}\}'
        md.inlinePatterns.register(
            SourceCollectorProcessor(source_pattern, md), 'source_collector', 160
        )

        # Citation pattern: [1], [2], etc.
        citation_pattern = r'\[(\d+)\]'
        md.inlinePatterns.register(
            SourceCitationProcessor(citation_pattern, md), 'source_citation', 155
        )

        # Finalize citations after the inline phase so manual references resolve correctly
        md.treeprocessors.register(
            SourceFinalizeTreeprocessor(md), 'source_finalize', 5
        )
