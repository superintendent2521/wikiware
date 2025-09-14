# WikiWare Utilities Documentation

## Overview

This document provides comprehensive documentation for the utility layer of WikiWare, which contains helper functions and components that support core application functionality. Utility functions are designed to be reusable across different parts of the application and provide consistent behavior for common operations.

The utility layer consists of the following components:
- `link_processor`: Processes internal wiki links and template variables in page content
- `logs`: Provides paginated log retrieval with filtering capabilities
- `markdown_extensions`: Extends Markdown parsing with custom syntax for internal links and color tags
- `template_processor`: Renders Jinja2 template variables in page content
- `validation`: Provides validation and sanitization functions for input data

## link_processor

Handles processing of internal links and template variables in page content.

### `async process_internal_links(content: str) -> str`

Processes internal links and template variables in page content.

**Parameters:**
- `content`: Raw page content containing potential `[[Page Title]]` links and `{{ variables }}` template syntax

**Returns:**
- Content with internal links converted to HTML anchors and template variables rendered

**Behavior:**
1. First renders any Jinja2 template variables (e.g., `{{ global.edits }}`) using the template processor
2. Then processes internal links in `[[Page Title]]` and `[[Page:Branch]]` format:
   - `[[Page Title]]` → `<a href="/page/Page%20Title">Page Title</a>`
   - `[[Page:Branch]]` → `<a href="/page/Page?branch=Branch">Page</a>`
3. URL-encodes spaces in titles and branch names
4. Uses "main" branch as default when no branch is specified

## logs

Provides utilities for retrieving and managing system logs.

### `async get_paginated_logs(page: int = 1, limit: int = 50, action_type: Optional[str] = None) -> Dict[str, Any]`

Retrieves paginated system logs with optional filtering by action type.

**Parameters:**
- `page`: Page number (1-indexed, default: 1)
- `limit`: Number of items per page (max 50, default: 50)
- `action_type`: Filter by action type ("edit", "branch_create", or None for all)

**Returns:**
Dictionary containing:
- `items`: List of log entries with structure:
  - `type`: "edit" or "branch_create"
  - `title`: Page title
  - `author`: Author name (or "System" for branch creation)
  - `branch`: Branch name
  - `timestamp`: UTC datetime of action
  - `action`: Action type ("page_edit" or "branch_create")
  - `details`: Additional context:
    - For edits: `content_length` (length of content)
    - For branch creation: `source_branch` (branch copied from)
- `total`: Total number of log items
- `page`: Current page number
- `pages`: Total number of pages
- `limit`: Items per page

**Behavior:**
- Validates parameters (limit capped at 50, page defaults to 1)
- Combines edit history and branch creation events
- Sorts all results by timestamp (newest first)
- Returns empty results if database is disconnected
- Returns empty results if page exceeds total pages

## markdown_extensions

Extends Markdown parsing with custom syntax support.

### `InternalLinkExtension`

Markdown extension that converts `[[Page Title]]` and `[[Page:Branch]]` syntax to HTML links.

**Behavior:**
- Uses priority 170 to ensure it runs before other inline patterns
- Converts `[[Page Title]]` to `<a href="/page/Page%20Title">Page Title</a>`
- Converts `[[Page:Branch]]` to `<a href="/page/Page?branch=Branch">Page</a>`
- URL-encodes spaces in titles and branch names
- Uses "main" branch as default when no branch is specified

### `ColorTagProcessor`

Processes `{{ global.color.{color} }}` syntax and converts to CSS color classes.

**Behavior:**
- Converts `{{ global.color.red }}` to `<span class="color-red"></span>`
- Converts `{{ global.color.green }}` to `<span class="color-green"></span>`
- Converts `{{ global.color.blue }}` to `<span class="color-blue"></span>`
- Converts `{{ global.color.purple }}` to `<span class="color-purple"></span>`
- Converts `{{ global.color.pink }}` to `<span class="color-pink"></span>`
- Converts `{{ global.color.orange }}` to `<span class="color-orange"></span>`
- Converts `{{ global.color.yellow }}` to `<span class="color-yellow"></span>`
- Converts `{{ global.color.gray }}` to `<span class="color-gray"></span>`
- Converts `{{ global.color.cyan }}` to `<span class="color-cyan"></span>`
- Uses priority 165 to ensure it runs before table extension

### `TableExtensionWrapper`

Wrapper extension that enables both table rendering and color tag processing.

**Behavior:**
- Registers the built-in Markdown table extension
- Registers the ColorTagProcessor with priority 165 to ensure proper ordering

## template_processor

Handles rendering of Jinja2 template variables in page content.

### `async render_template_content(content: str, request: dict = None) -> str`

Renders Jinja2 template variables in page content.

**Parameters:**
- `content`: Raw page content (Markdown) containing template variables
- `request`: Optional request context (used for user, branch, etc.)

**Returns:**
- Content with template variables rendered

**Supported Variables:**
- `{{ global.edits }}`: Total number of edits
- `{{ global.pages }}`: Total number of pages
- `{{ global.characters }}`: Total number of characters
- `{{ global.images }}`: Total number of images
- `{{ global.last_updated }}`: Timestamp of last update
- `{{ global.color.{color} }}`: Color tag for table cells (returns HTML span with CSS class)

**Color Classes:**
- `color-red`
- `color-green`
- `color-blue`
- `color-purple`
- `color-pink`
- `color-orange`
- `color-gray`
- `color-yellow`
- `color-cyan`

**Behavior:**
- Fetches global statistics from the database
- Creates context with `global` and `request` objects
- Uses keyword expansion (`**context`) to render variables
- Returns original content unchanged if database is disconnected
- Returns original content unchanged if rendering fails (fail-safe)

## validation

Provides validation and sanitization functions for input data.

### `is_valid_title(title: str) -> bool`

Validates a page title to prevent path traversal and other security issues.

**Parameters:**
- `title`: The title to validate

**Returns:**
- `True` if title is valid
- `False` if title is empty, contains "..", or starts with "/"

### `is_valid_branch_name(branch_name: str) -> bool`

Validates a branch name to ensure it's safe and follows naming conventions.

**Parameters:**
- `branch_name`: The branch name to validate

**Returns:**
- `True` if branch name is valid
- `False` if branch name is empty, contains "..", "/", or "\", or is a reserved name

**Reserved Names:**
- "main"
- "master"
- "head"
- "origin"

### `sanitize_filename(filename: str) -> str`

Sanitizes a filename to prevent security issues.

**Parameters:**
- `filename`: The filename to sanitize

**Returns:**
- Sanitized filename with dangerous characters replaced by underscores

**Dangerous Characters Replaced:**
- `/`
- `\`
- `:`
- `*`
- `?`
- `"`
- `<`
- `>`
- `|`
