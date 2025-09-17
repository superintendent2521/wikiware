# WikiWare Services Documentation

## Overview

This document provides comprehensive documentation for the service layer of WikiWare, which contains the business logic for core application operations. Services act as intermediaries between the route handlers and the database layer, encapsulating complex operations and ensuring clean separation of concerns.

The service layer consists of the following components:
- `PageService`: Manages page creation, retrieval, updates, and search
- `BranchService`: Handles branch creation, management, and page branching operations
- `UserService`: Manages user authentication, registration, and session handling
- `LogService`: Collects and manages historical page data

## PageService

Handles all operations related to wiki pages.

### `get_page(title: str, branch: str = "main") -> Optional[Dict[str, Any]]`

Retrieves a specific page by title and branch.

**Parameters:**
- `title`: The title of the page to retrieve
- `branch`: The branch name (default: "main")

**Returns:**
- Page document as a dictionary if found
- `None` if page doesn't exist or database connection fails

### `create_page(title: str, content: str, author: str = "Anonymous", branch: str = "main") -> bool`

Creates a new page with the specified content.

**Parameters:**
- `title`: The title of the new page
- `content`: The content of the page
- `author`: The name of the author (default: "Anonymous")
- `branch`: The branch to create the page in (default: "main")

**Returns:**
- `True` if page was created successfully
- `False` if creation failed (e.g., database connection issues)

### `update_page(title: str, content: str, author: str = "Anonymous", branch: str = "main") -> bool`

Updates an existing page or creates it if it doesn't exist.

**Parameters:**
- `title`: The title of the page to update
- `content`: The new content for the page
- `author`: The name of the author making the update (default: "Anonymous")
- `branch`: The branch containing the page (default: "main")

**Returns:**
- `True` if page was updated or created successfully
- `False` if operation failed

### `get_pages_by_branch(branch: str = "main", limit: int = 100) -> List[Dict[str, Any]]`

Retrieves all pages in a specific branch, sorted by update time (newest first).

**Parameters:**
- `branch`: The branch name to retrieve pages from (default: "main")
- `limit`: Maximum number of pages to return (default: 100)

**Returns:**
- List of page documents

### `search_pages(query: str, branch: str = "main", limit: int = 100) -> List[Dict[str, Any]]`

Searches pages by title or content using case-insensitive regex matching.

**Parameters:**
- `query`: Search term to match against page titles and content
- `branch`: The branch to search in (default: "main")
- `limit`: Maximum number of results to return (default: 100)

**Returns:**
- List of matching page documents

### `delete_page(title: str) -> bool`

Deletes all branches of a page (effectively deleting the page entirely).

**Parameters:**
- `title`: The title of the page to delete

**Returns:**
- `True` if all branches of the page were successfully deleted
- `False` if deletion failed (e.g., page doesn't exist or database connection issues)

### `delete_branch(title: str, branch: str) -> bool`

Deletes a specific branch from a specific page.

**Parameters:**
- `title`: The title of the page
- `branch`: The branch name to delete from the page

**Returns:**
- `True` if the specified branch was successfully deleted
- `False` if deletion failed (e.g., branch doesn't exist or database connection issues)

## BranchService

Manages branch operations for version control and collaborative editing.

### `get_available_branches() -> List[str]`

Retrieves all available branches across the wiki.

**Returns:**
- List of branch names (always includes "main" as default)

### `get_branches_for_page(title: str) -> List[str]`

Retrieves all branches that contain a specific page.

**Parameters:**
- `title`: The title of the page to check

**Returns:**
- List of branch names containing the page (always includes "main")

### `create_branch(title: str, branch_name: str, source_branch: str = "main") -> bool`

Creates a new branch for a page by copying content from a source branch.

**Parameters:**
- `title`: The title of the page to branch
- `branch_name`: The name of the new branch to create
- `source_branch`: The branch to copy content from (default: "main")

**Returns:**
- `True` if branch was created successfully
- `False` if branch already exists or operation failed

### `set_branch(branch: str) -> str`

Sets the current branch for session management.

**Parameters:**
- `branch`: The branch name to set

**Returns:**
- The branch name that was set

## UserService

Handles user authentication, registration, and session management.

### `hash_password(password: str) -> str`

Hashes a plain text password using Argon2id or bcrypt.

**Parameters:**
- `password`: The plain text password to hash

**Returns:**
- The hashed password string

### `verify_password(plain_password: str, hashed_password: str) -> bool`

Verifies a plain text password against its hash.

**Parameters:**
- `plain_password`: The plain text password to verify
- `hashed_password`: The stored hashed password

**Returns:**
- `True` if password matches
- `False` if password doesn't match

### `get_user_by_username(username: str) -> Optional[Dict[str, Any]]`

Retrieves a user by username.

**Parameters:**
- `username`: The username to search for

**Returns:**
- User document if found
- `None` if user doesn't exist or database connection fails

### `create_user(user_data: UserRegistration) -> Optional[Dict[str, Any]]`

Creates a new user account.

**Parameters:**
- `user_data`: User registration data containing username and password

**Returns:**
- Created user document if successful
- `None` if username already exists or operation failed

### `authenticate_user(username: str, password: str, client_ip: str = "unknown", user_agent: str = "unknown") -> Optional[Dict[str, Any]]`

Authenticates a user by username and password.

**Parameters:**
- `username`: The username to authenticate
- `password`: The plain text password
- `client_ip`: Client IP address for logging (default: "unknown")
- `user_agent`: User agent string for logging (default: "unknown")

**Returns:**
- User document if authentication successful
- `None` if authentication failed (invalid credentials, inactive account, etc.)

### `create_session(user_id: str) -> Optional[str]`

Creates a new session for a user.

**Parameters:**
- `user_id`: The user ID to create a session for

**Returns:**
- Session ID string if successful
- `None` if session creation failed

### `get_session(session_id: str) -> Optional[Dict[str, Any]]`

Retrieves a session by session ID and validates expiration.

**Parameters:**
- `session_id`: The session ID to retrieve

**Returns:**
- Session document if valid and not expired
- `None` if session doesn't exist or has expired

### `delete_session(session_id: str) -> bool`

Deletes a session.

**Parameters:**
- `session_id`: The session ID to delete

**Returns:**
- `True` if session was deleted
- `False` if deletion failed

### `get_user_by_session(session_id: str) -> Optional[Dict[str, Any]]`

Retrieves a user by session ID.

**Parameters:**
- `session_id`: The session ID to look up

**Returns:**
- User document if session exists and is valid
- `None` if session doesn't exist or is invalid

## LogService

Manages historical page data collection.

### `collect_all() -> List`

Retrieves all historical page data from the history collection.

**Returns:**
- List of all history records
- Empty list if history collection is unavailable
