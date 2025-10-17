# API Routes Documentation

This document provides detailed information about the API routes available in WikiWare.

## Table of Contents

- [Exports API](#exports-api)
- [History API](#history-api)
- [Images API](#images-api)
- [Logs API](#logs-api)
- [PDF API](#pdf-api)
- [Stats API](#stats-api)
- [Uploads API](#uploads-api)

---

## Exports API

### Download Collections

Download wiki collections as a ZIP file.

- **Endpoint:** `GET /exports/collections`
- **Authentication:** Required
- **Description:** Allows an authenticated user to download wiki collections as a ZIP file.
- **Response:**
  - **200 OK:** ZIP file download
    - Headers: `Content-Disposition: attachment; filename="filename.zip"`
    - Content-Type: `application/zip`
  - **429 Too Many Requests:** Rate limit exceeded
    - Headers: `Retry-After: <seconds>`
    - Body: `"You can only export collections once every 24 hours"`
  - **503 Service Unavailable:** Export temporarily unavailable
    - Body: `"Collection export is temporarily unavailable"`
  - **404 Not Found:** Account not found
    - Body: `"Account not found"`

---

## History API

### Get History Versions

Return recent history entries for a requested page.

- **Endpoint:** `GET /history/{title}`
- **Authentication:** Not required
- **Parameters:**
  - `title` (path): The title of the page
  - `branch` (query, optional): The branch name (default: "main")
  - `limit` (query, optional): Maximum number of versions to return (1-50, default: 10)
- **Response:**
  - **200 OK:**
    ```json
    {
      "title": "Page Title",
      "branch": "main",
      "versions": [
        {
          "index": 1,
          "display_number": "1",
          "author": "Author Name",
          "updated_at": "2023-01-01 12:00:00",
          "is_current": true,
          "view_url": "/history/Page%20Title/1",
          "history_url": "/history/Page%20Title",
          "compare_url": "/history/Page%20Title/compare",
          "label": "Version 1 — Author Name (2023-01-01 12:00:00)"
        }
      ]
    }
    ```
  - **400 Bad Request:** Invalid page title or branch parameter
    - Body: `"Invalid page title"` or `"Invalid branch parameter"`
  - **503 Service Unavailable:** Database not available
    - Body: `"Database not available"`
  - **500 Internal Server Error:** Failed to fetch history data
    - Body: `"Failed to fetch history data"`

---

## Images API

### List Images

Return JSON list of uploaded images.

- **Endpoint:** `GET /images`
- **Authentication:** Required
- **Response:**
  - **200 OK:**
    ```json
    {
      "items": [
        "image1.jpg",
        "image2.png"
      ]
    }
    ```

---

## Logs API

### Get Logs

Get paginated system logs with optional filtering.

- **Endpoint:** `GET /api/logs`
- **Authentication:** Required (can be bypassed with special permissions)
- **Parameters:**
  - `page` (query, optional): Page number (default: 1)
  - `limit` (query, optional): Number of items per page (default: 50)
  - `bypass` (query, optional): Bypass certain restrictions (requires special permissions)
  - `action_type` (query, optional): Filter by action type
  - Request body (optional): Can include pagination and filter parameters as JSON
- **Response:**
  - **200 OK:**
    ```json
    {
      "logs": [
        {
          "id": "log_id",
          "action": "edit",
          "user": "username",
          "timestamp": "2023-01-01T12:00:00Z"
        }
      ],
      "total": 100,
      "page": 1,
      "limit": 50
    }
    ```
  - **500 Internal Server Error:** Internal server error
    - Body: `"Internal server error"`

---

## PDF API

### Generate Page PDF

Generate a PDF for a requested page.

- **Endpoint:** `POST /pdf/page`
- **Authentication:** Currently commented out (may require authentication in production)
- **Request Body:**
  ```json
  {
    "title": "Page Title",
    "branch": "main",
    "depth": 1
  }
  ```
- **Parameters:**
  - `title`: The title of the page to include in the PDF
  - `branch` (optional): The branch name (default: "main")
  - `depth` (optional): How many linked pages to include (1-5, default: 1)
- **Response:**
  - **200 OK:** PDF file download
    - Headers: `Content-Disposition: attachment; filename="filename.pdf"`
    - Content-Type: `application/pdf`
  - **404 Not Found:** Page not found
    - Body: `"Page not found"`
  - **500 Internal Server Error:** Failed to generate PDF
    - Body: `"Failed to generate PDF"`

---

## Stats API

### Get User Statistics

Get edit statistics for a specific user.

- **Endpoint:** `GET /stats/{username}`
- **Authentication:** Not required
- **Parameters:**
  - `username` (path): Username to get statistics for
- **Response:**
  - **200 OK:**
    ```json
    {
      "total_edits": 42,
      "page_edits": {
        "Page1": 5,
        "Page2": 10
      }
    }
    ```
  - **404 Not Found:** User not found
    - Body: `"User not found"`
  - **503 Service Unavailable:** Database not available
    - Body: `"Database not available"`
  - **500 Internal Server Error:** Internal server error
    - Body: `"Internal server error"`

---

## Uploads API

### Upload Image

Upload an image file.

- **Endpoint:** `POST /upload-image`
- **Authentication:** Required
- **Request Body:** Multipart form data with file
- **Parameters:**
  - `file`: The image file to upload
- **Response:**
  - **200 OK:** Upload successful
    ```json
    {
      "url": "https://example.com/image.jpg",
      "filename": "image.jpg"
    }
    ```
  - **200 OK:** Duplicate file (returns existing file URL)
    ```json
    {
      "url": "https://example.com/existing-image.jpg",
      "filename": "existing-image.jpg"
    }
    ```
  - **400 Bad Request:** Invalid file type, file too large, or invalid filename
    ```json
    {
      "error": "Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed."
    }
    ```
  - **403 Forbidden:** Image uploading is disabled
    ```json
    {
      "error": "Image uploading is currently disabled by an administrator."
    }
    ```
  - **500 Internal Server Error:** Failed to store the image
    ```json
    {
      "error": "Failed to store the image. Please try again later."
    }
    ```

**Allowed File Types:**
- JPEG (image/jpeg)
- PNG (image/png)
- GIF (image/gif)
- WebP (image/webp)

**Maximum File Size:** Configurable via MAX_FILE_SIZE (default: 16MB)

---

## Authentication

Most API endpoints require authentication. The authentication is handled through a token-based system implemented in the `AuthMiddleware`. When authentication is required, the API will return a 401 Unauthorized response if the user is not authenticated.

## Error Handling

All API endpoints follow consistent error handling patterns:

- **4xx Errors:** Client errors (bad requests, unauthorized, not found, etc.)
- **5xx Errors:** Server errors (internal server error, service unavailable, etc.)
- Error responses typically include a descriptive message in the response body

## Rate Limiting

Some endpoints implement rate limiting to prevent abuse. When rate limits are exceeded, the API returns a 429 Too Many Requests response with a Retry-After header indicating how long to wait before making another request.
