# WikiWare Documentation Index

Welcome to the WikiWare documentation hub. This guide helps you navigate the system's architecture and functionality.

## Table of Contents

### Core Components
- [Middleware](middleware.md) - Authentication and session handling
- [Services](services.md) - Business logic and data operations
- [Utils](utils.md) - Helper functions and utilities
- [Routes](routes.md) - API endpoints and web routes

### Component Details

#### Middleware
The middleware layer handles cross-cutting concerns like authentication and session management. Key components:
- `auth_middleware`: Manages user authentication, session validation, and authorization checks
- Functions: `get_current_user()`, `require_auth()`, `is_admin()`

#### Services
The service layer contains business logic that interfaces with the database:
- `PageService`: Manages page creation, retrieval, updates, and search
- `BranchService`: Handles branch creation and management
- `UserService`: Manages user registration, authentication, and sessions
- `LogService`: Collects and manages historical page data

#### Utilities
Utility functions provide reusable helpers across the application:
- `link_processor`: Processes internal wiki links and template variables
- `logs`: Provides paginated log retrieval with filtering
- `markdown_extensions`: Extends Markdown with custom syntax for links and colors
- `template_processor`: Renders Jinja2 template variables in content
- `validation`: Validates and sanitizes input data

#### Routes
All API endpoints organized by functionality:
- **Authentication**: `/register`, `/login`, `/logout`
- **Page Management**: `/`, `/page/{title}`, `/edit/{title}`, `/delete/{title}`
- **History**: `/history/{title}`, `/restore/{title}/{version_index}`
- **Branches**: `/branches/{title}`, `/set-branch`
- **Search**: `/search`
- **Admin**: `/admin`
- **Images**: `/images`, `/api/images`, `/upload-image`
- **Statistics**: `/stats`
- **Logs**: `/api/logs`
- **Uploads**: `/upload-image`

### Getting Started
- Start with [Middleware](middleware.md) to understand authentication flow
- Review [Routes](routes.md) to see available endpoints
- Use [Services](services.md) to understand data operations
- Consult [Utils](utils.md) for helper functions and formatting rules

All documentation is maintained in the `docs/` directory and reflects the current system state.
