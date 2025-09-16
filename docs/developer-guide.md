# WikiWare Developer Guide

This document provides comprehensive technical documentation for developers working on WikiWare. It covers the architecture, core components, and implementation details necessary to understand, extend, and maintain the system.

## Architecture Overview

WikiWare follows a clean separation of concerns architecture with three primary layers:

1. **Services** - Business logic that interacts with the database
2. **Middleware** - Cross-cutting concerns like authentication and security
3. **Utils** - Reusable helper functions and utilities

The system is built on FastAPI with a MongoDB backend. All components are designed to be modular and testable.

## Core Components

### Middleware Layer

The middleware layer handles authentication, session management, and security headers.

#### `auth_middleware.py`

- **`get_current_user(request: Request)`**: Retrieves authenticated user from session cookie
  - Checks for session cookie in order: `SESSION_COOKIE_NAME`, `__Host-user_session`, `user_session`
  - Returns user data (username, is_admin) or None if unauthenticated
  - Does not raise exceptions - returns None for unauthenticated users
  - Logs warnings for validation errors

- **`require_auth(request: Request)`**: Enforces authentication on routes
  - Uses `get_current_user()` internally
  - Returns user data if authenticated
  - Raises HTTPException 401 if authentication fails
  - Used as a dependency in route handlers

- **`is_admin(request: Request)`**: Checks if user has admin privileges
  - Uses `get_current_user()` internally
  - Returns True if user is authenticated and has is_admin=True
  - Returns False if user is unauthenticated or not an admin
  - Does not raise exceptions

#### `security_headers.py`

- Adds security headers to all responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `X-XSS-Protection: 1; mode=block`
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`

### Services Layer

Services encapsulate business logic and database operations.

#### `PageService`
- `get_page(title: str, branch: str = "main")`: Retrieve page by title and branch
- `create_page(title: str, content: str, author: str = "Anonymous", branch: str = "main")`: Create new page
- `update_page(title: str, content: str, author: str = "Anonymous", branch: str = "main")`: Update or create page
- `get_pages_by_branch(branch: str = "main", limit: int = 100)`: Get all pages in branch
- `search_pages(query: str, branch: str = "main", limit: int = 100)`: Search pages by content

#### `BranchService`
- `get_available_branches()`: Get all branches in system
- `get_branches_for_page(title: str)`: Get branches containing specific page
- `create_branch(title: str, branch_name: str, source_branch: str = "main")`: Create new branch from source
- `set_branch(branch: str)`: Set session branch preference

#### `UserService`
- `hash_password(password: str)`: Hash password using Argon2id
- `verify_password(plain_password: str, hashed_password: str)`: Verify password hash
- `get_user_by_username(username: str)`: Get user by username
- `create_user(user_data: UserRegistration)`: Create new user account
- `authenticate_user(username: str, password: str, client_ip: str = "unknown", user_agent: str = "unknown")`: Authenticate user
- `create_session(user_id: str)`: Create session for user
- `get_session(session_id: str)`: Validate and retrieve session
- `delete_session(session_id: str)`: Delete session
- `get_user_by_session(session_id: str)`: Get user by session ID

#### `LogService`
- `collect_all()`: Retrieve all historical page data

### Utils Layer

Utility functions provide reusable helpers across the application.

#### `link_processor.py`
- `process_internal_links(content: str)`: Convert `[[Page Title]]` and `[[Page:Branch]]` to HTML links
  - Renders template variables first
  - Converts links to `/page/{title}?branch={branch}` format
  - URL-encodes spaces in titles and branch names

#### `logs.py`
- `get_paginated_logs(page: int = 1, limit: int = 50, action_type: Optional[str] = None)`: Get paginated logs
  - Combines edit history and branch creation events
  - Returns structured JSON with pagination metadata
  - Limits limit to 50 for performance

#### `markdown_extensions.py`
- `InternalLinkExtension`: Markdown extension for `[[Page Title]]` syntax
- `ColorTagProcessor`: Processes `{{ global.color.{color} }}` to CSS classes
- `TableExtensionWrapper`: Wrapper that enables both table rendering and color tags

#### `template_processor.py`
- `render_template_content(content: str, request: dict = None)`: Render Jinja2 template variables
  - Supported variables: `{{ global.edits }}`, `{{ global.pages }}`, `{{ global.characters }}`, `{{ global.images }}`, `{{ global.last_updated }}`, `{{ global.color.{color} }}`
  - Fetches global statistics from database
  - Returns original content if database is disconnected

#### `validation.py`
- `is_valid_title(title: str)`: Validate page title (no "..", no "/", no empty)
- `is_valid_branch_name(branch_name: str)`: Validate branch name (no "..", no "/", no "\", not reserved)
- `sanitize_filename(filename: str)`: Sanitize uploaded filenames (replace dangerous chars with underscores)

## Development Workflow

### Setting Up
1. Install dependencies: `pip install -r requirements.txt`
2. Start MongoDB: `mongod` (ensure it's running on default port)
3. Run server: `python index.py`
4. Access at: http://localhost:8000

### Adding New Features
1. **Implement service**: Add business logic in `src/services/`
2. **Create utility**: Add helper functions in `src/utils/` if needed
3. **Update middleware**: Add authentication/security if required
4. **Update documentation**: Add to `docs/` directory

### Testing
- Run tests: `python -m pytest tests/`
- Test manually via browser and API clients
- Verify authentication flows work correctly
- Test edge cases (invalid titles, branch names, etc.)

## Security Considerations

- All user input is validated and sanitized
- Passwords are hashed using Argon2id
- Session cookies use `HttpOnly`, `Secure`, and `SameSite=Lax`
- File uploads are validated by magic bytes and filename sanitization
- All routes requiring authentication use `require_auth` dependency
- Admin routes require `is_admin` check
- XSS protection headers are enforced
- CSRF protection is enabled for forms

## Database Schema

### Pages Collection
```json
{
  "_id": "ObjectId",
  "title": "string",
  "content": "string",
  "author": "string",
  "branch": "string",
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

### Users Collection
```json
{
  "_id": "ObjectId",
  "username": "string",
  "password_hash": "string",
  "is_admin": "boolean",
  "created_at": "ISODate",
  "last_login": "ISODate"
}
```

### Sessions Collection
```json
{
  "_id": "ObjectId",
  "user_id": "ObjectId",
  "session_id": "string",
  "created_at": "ISODate",
  "expires_at": "ISODate",
  "ip_address": "string",
  "user_agent": "string"
}
```

### History Collection
```json
{
  "_id": "ObjectId",
  "title": "string",
  "content": "string",
  "author": "string",
  "branch": "string",
  "timestamp": "ISODate",
  "version": "integer"
}
```

### Logs Collection
```json
{
  "_id": "ObjectId",
  "action": "string", // "edit" or "branch_create"
  "title": "string",
  "branch": "string",
  "author": "string",
  "timestamp": "ISODate",
  "details": "object" // Contains additional context
}
```

## Troubleshooting

### Common Issues
- **"Session expired"**: Clear browser cookies or restart server
- **"Authentication required"**: Ensure you're logged in
- **"Page not found"**: Check title spelling and branch name
- **"Invalid branch name"**: Branch names can't contain special characters
- **"File upload failed"**: Check file type and size (max 10MB)

### Debugging Tips
- Check server logs in `logs/` directory
- Use browser developer tools to inspect network requests
- Verify MongoDB is running and accessible
- Test individual components in isolation using Python REPL

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Update documentation in `docs/`
5. Run tests
6. Submit a pull request

All contributions must include updated documentation and pass all tests.
