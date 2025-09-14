# WikiWare Middleware Documentation

## Overview

This document provides comprehensive documentation for the middleware layer of WikiWare, which handles cross-cutting concerns such as authentication and session management. Middleware components intercept requests and responses to enforce security policies and provide contextual data to route handlers.

The middleware layer consists of the following component:
- `auth_middleware`: Handles user authentication, session validation, and authorization checks

## auth_middleware

Handles authentication and authorization for API requests.

### `async get_current_user(request: Request) -> Optional[Dict[str, Any]]`

Retrieves the current authenticated user from the session cookie.

**Parameters:**
- `request`: FastAPI request object containing HTTP headers and cookies

**Returns:**
- Dictionary with user information if authenticated:
  - `username`: The username of the authenticated user
  - `is_admin`: Boolean indicating if the user has admin privileges
- `None` if:
  - No valid session cookie is present
  - Database is disconnected
  - Session is invalid or user is inactive

**Session Cookie Handling:**
- Checks for session cookie in the following order:
  1. `SESSION_COOKIE_NAME` (configured value)
  2. `__Host-user_session`
  3. `user_session`

**Behavior:**
- Returns `None` if database is disconnected (offline mode)
- Returns `None` if session ID is invalid or user is inactive
- Logs warnings for any errors during session validation
- Does not raise exceptions - returns `None` for unauthenticated users

### `async require_auth(request: Request) -> Dict[str, Any]`

Requires authentication for a request and returns user data if authenticated.

**Parameters:**
- `request`: FastAPI request object containing HTTP headers and cookies

**Returns:**
- Dictionary with user information if authenticated:
  - `username`: The username of the authenticated user
  - `is_admin`: Boolean indicating if the user has admin privileges

**Raises:**
- `HTTPException` with status code 401 and detail "Authentication required" if:
  - No valid session cookie is present
  - Database is disconnected
  - Session is invalid or user is inactive

**Behavior:**
- Uses `get_current_user()` internally to validate the session
- Returns the same user data structure as `get_current_user()` if authentication succeeds
- Used as a dependency in route handlers to enforce authentication requirements

### `async is_admin(request: Request) -> bool`

Checks if the current user has admin privileges.

**Parameters:**
- `request`: FastAPI request object containing HTTP headers and cookies

**Returns:**
- `True` if user is authenticated and has admin privileges
- `False` if:
  - User is not authenticated
  - User is authenticated but does not have admin privileges
  - Database is disconnected

**Behavior:**
- Uses `get_current_user()` internally to retrieve user data
- Returns `False` if user is not authenticated
- Returns `False` if user is authenticated but `is_admin` flag is not set or is `False`
- Does not raise exceptions - returns boolean result for easy conditional checks
