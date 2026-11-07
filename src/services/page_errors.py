"""Failures that callers must distinguish from absent wiki content."""


class PageServiceError(Exception):
    """A page operation failed with a user-facing reason."""

    status_code = 500


class PageUnavailableError(PageServiceError):
    """The persistence layer could not complete a page operation."""

    status_code = 503

    def __init__(self, message="Wiki data is temporarily unavailable. Please try again."):
        super().__init__(message)


class PageConflictError(PageServiceError):
    """The editor's starting version no longer matches the stored page."""

    status_code = 409

    def __init__(self, message="This page changed since you opened it. Your draft is kept; open the latest page before merging your changes."):
        super().__init__(message)


class PagePermissionError(PageServiceError):
    """The current account cannot perform a page mutation."""

    status_code = 403


def page_version_token(page: dict | None) -> str:
    """Identify the exact branch state shown to an editor without changing schema."""
    if page is None:
        return "new"
    return f'{page["id"]}:{page["updated_at"].isoformat()}'
