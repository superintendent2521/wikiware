# WikiWare Route Documentation

## Overview

This document provides comprehensive documentation for all API routes in WikiWare. Routes are organized by functionality and follow RESTful conventions. All routes are served via FastAPI and require appropriate authentication where specified.

---

## Authentication Routes

### `GET /register`
- **Description**: Displays the user registration form.
- **Authentication**: Public access
- **Response**: HTML form with CSRF token
- **Notes**: 
  - Uses `fastapi_csrf_protect` for CSRF protection
  - Redirects to `/` on successful registration

### `POST /register`
- **Description**: Creates a new user account.
- **Authentication**: Public access
- **Request Body**: Form data with `username`, `password`, `confirm_password`
- **Response**: 
  - `303 Redirect` to `/` on success
  - HTML form with error message on failure
- **Validation**:
  - Passwords must match
  - Username must be unique
  - Database connection required

### `GET /login`
- **Description**: Displays the login form.
- **Authentication**: Public access
- **Query Parameters**: `next` (optional redirect path after login)
- **Response**: HTML form with CSRF token
- **Notes**: 
  - Uses `fastapi_csrf_protect` for CSRF protection
  - Redirects to `next` parameter or `/` on success

### `POST /login`
- **Description**: Authenticates user and creates session.
- **Authentication**: Public access
- **Request Body**: Form data with `username`, `password`, `next` (optional)
- **Response**: 
  - `303 Redirect` to `next` or `/` on success
  - HTML form with error message on failure
- **Security**:
  - Logs login attempts to `logs/login_activity.log`
  - Sets secure session cookie with 1-week expiry
  - Uses `httponly` and `samesite=Lax` for cookie security

### `POST /logout`
- **Description**: Destroys user session and logs out.
- **Authentication**: Requires active session
- **Response**: `303 Redirect` to `/`
- **Notes**: 
  - Deletes session from database
  - Clears session cookie

---

## Page Routes

### `GET /`
- **Description**: Displays the Home page.
- **Authentication**: Public access
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page with rendered Markdown content
- **Notes**: 
  - Home page is hardcoded in the system
  - Uses `process_internal_links` to resolve wiki-style links

### `GET /page/{title}`
- **Description**: Displays a specific page.
- **Authentication**: Public access
- **Path Parameters**: `title` (page title)
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page with rendered Markdown content
- **Notes**: 
  - Redirects to edit page if page doesn't exist
  - Uses `process_internal_links` to resolve wiki-style links
  - Shows branch selector if multiple branches exist

### `GET /edit/{title}`
- **Description**: Displays the page editor.
- **Authentication**: Requires authentication
- **Path Parameters**: `title` (page title)
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML editor form with pre-filled content
- **Notes**: 
  - Redirects to login if not authenticated
  - Shows global statistics in editor UI
  - Shows branch selector if multiple branches exist

### `POST /edit/{title}`
- **Description**: Saves changes to a page.
- **Authentication**: Requires authentication
- **Path Parameters**: `title` (page title)
- **Request Body**: Form data with `content`, `author`, `branch`
- **Response**: `303 Redirect` to `/page/{title}` with `updated=true` parameter
- **Notes**: 
  - Author is automatically set to authenticated username
  - Updates page in database and creates history entry

### `POST /delete/{title}`
- **Description**: Deletes a page.
- **Authentication**: Requires admin privileges
- **Path Parameters**: `title` (page title)
- **Request Body**: Form data with `branch` (default: "main")
- **Response**: `303 Redirect` to `/`
- **Notes**: 
  - Only users with `is_admin=True` can delete pages
  - Deletes page from database permanently

---

## History Routes

### `GET /history/{title}`
- **Description**: Displays the version history of a page.
- **Authentication**: Public access
- **Path Parameters**: `title` (page title)
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page listing all versions with timestamps and authors
- **Notes**: 
  - Shows current version as first entry
  - Shows all historical versions sorted by date (newest first)

### `GET /history/{title}/{version_index}`
- **Description**: Displays a specific version of a page.
- **Authentication**: Public access
- **Path Parameters**: `title` (page title), `version_index` (0 = current, 1+ = historical)
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page with rendered content of the selected version
- **Notes**: 
  - `version_index=0` shows current version
  - `version_index=1` shows most recent historical version
  - Uses `process_internal_links` to resolve wiki-style links

### `POST /restore/{title}/{version_index}`
- **Description**: Restores a page to a previous version.
- **Authentication**: Requires authentication
- **Path Parameters**: `title` (page title), `version_index` (0 = current, 1+ = historical)
- **Request Body**: Form data with `branch` (default: "main")
- **Response**: `303 Redirect` to `/page/{title}?branch={branch}&restored=true`
- **Notes**: 
  - Current version is saved to history before restoration
  - Only authenticated users can restore versions

---

## Branch Routes

### `GET /branches/{title}`
- **Description**: Lists all branches for a page.
- **Authentication**: Public access
- **Path Parameters**: `title` (page title)
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page with branch selector
- **Notes**: 
  - Used in edit page to show available branches
  - Redirects to edit page if page doesn't exist

### `POST /branches/{title}/create`
- **Description**: Creates a new branch for a page.
- **Authentication**: Requires authentication
- **Path Parameters**: `title` (page title)
- **Request Body**: Form data with `branch_name`, `source_branch` (default: "main")
- **Response**: `303 Redirect` to `/page/{title}?branch={branch_name}`
- **Notes**: 
  - Branch name must be valid (alphanumeric + hyphens/underscores)
  - Source branch must exist

### `POST /set-branch`
- **Description**: Sets the global branch preference for the session.
- **Authentication**: Requires authentication
- **Request Body**: Form data with `branch`
- **Response**: `303 Redirect` to referring page with updated branch parameter
- **Notes**: 
  - Updates branch parameter in URL
  - Does not persist across sessions

---

## Search Routes

### `GET /search`
- **Description**: Searches for pages by keyword.
- **Authentication**: Public access
- **Query Parameters**: `q` (search query), `branch` (default: "main")
- **Response**: HTML page with search results
- **Notes**: 
  - Performs case-insensitive substring search
  - Results include page title and snippet
  - Shows branch selector if multiple branches exist

---

## Admin Routes

### `GET /admin`
- **Description**: Displays the admin dashboard.
- **Authentication**: Requires admin privileges (`is_admin=True`)
- **Response**: HTML dashboard with user management and statistics
- **Notes**: 
  - Shows list of all users with admin status
  - Displays system statistics (total edits, pages, characters)
  - Shows recent system logs (last 5 entries)
  - Redirects to access denied page if not admin

---

## Image Routes

### `GET /images`
- **Description**: Displays the image library UI.
- **Authentication**: Requires authentication
- **Query Parameters**: `q` (optional filename filter)
- **Response**: HTML page listing uploaded images
- **Notes**: 
  - Shows image thumbnails with filename, size, and modification date
  - Supports filtering by filename

### `GET /api/images`
- **Description**: Returns JSON list of uploaded images.
- **Authentication**: Requires authentication
- **Response**: JSON object with `items` array containing image metadata
- **Structure**:
  ```json
  {
    "items": [
      {
        "filename": "image.png",
        "url": "/static/uploads/image.png",
        "size": 12345,
        "modified": 1728901234
      }
    ]
  }
  ```

### `POST /upload-image`
- **Description**: Uploads an image file.
- **Authentication**: Requires authentication
- **Request Body**: Form data with `file` (image file)
- **Response**: 
  - `200 OK` with JSON containing `url` and `filename` on success
  - `400 Bad Request` with error message on validation failure
  - `500 Internal Server Error` on upload failure
- **Validation**:
  - Only allows: JPEG, PNG, GIF, WebP
  - Validates file signature (magic bytes)
  - Limits file size to 10MB
  - Sanitizes filenames and generates unique UUID-based filenames

---

## Statistics Routes

### `GET /stats`
- **Description**: Displays system statistics.
- **Authentication**: Public access
- **Query Parameters**: `branch` (default: "main")
- **Response**: HTML page with system metrics
- **Metrics**:
  - Total edits
  - Total pages
  - Total characters
  - Total images
  - Last updated timestamp
- **Notes**: 
  - Uses global context processor to inject stats into all templates

### `GET /stats/{username}`
- **Description**: Displays User Statistics
- **Authentication**: Public access
- **Query Parameters**: Username
- **Response**: Json with total edits and Users top edited pages.
- **Metrics**:
  - Total edits
  - Top edited pages.

---

## Logs Routes

### `GET /api/logs`
- **Description**: Returns paginated system logs.
- **Authentication**: Requires authentication
- **Query Parameters**:
  - `page` (default: 1)
  - `limit` (default: 50, max: 50)
  - `action_type` (optional: "edit", "branch_create", or null for all)
- **Response**: JSON object with:
  - `items`: Array of log entries
  - `total`: Total number of log entries
  - `page`: Current page number
  - `pages`: Total number of pages
  - `limit`: Items per page
- **Log Entry Structure**:
  ```json
  {
    "id": "unique_id",
    "action": "edit",
    "title": "Page Title",
    "branch": "main",
    "author": "username",
    "timestamp": "2025-09-14T12:58:00Z",
    "details": "Additional context"
  }
  ```
- **Notes**: 
  - Logs include page edits and branch creations
  - Only accessible to authenticated users

---

## Upload Routes

### `POST /upload-image`
- **Description**: Uploads an image file (same as Image Routes).
- **Authentication**: Requires authentication
- **Request Body**: Form data with `file` (image file)
- **Response**: 
  - `200 OK` with JSON containing `url` and `filename` on success
  - `400 Bad Request` with error message on validation failure
  - `500 Internal Server Error` on upload failure
- **Validation**:
  - Only allows: JPEG, PNG, GIF, WebP
  - Validates file signature (magic bytes)
  - Limits file size to 10MB
  - Sanitizes filenames and generates unique UUID-based filenames
