"""
Page data models and validation for WikiWare.
"""

from pydantic import BaseModel, validator
from typing import Optional
from datetime import datetime, timezone
from ..utils.validation import is_valid_title


class WikiPage(BaseModel):
    """Model for wiki page data."""
    title: str
    content: str
    author: Optional[str] = "Anonymous"
    branch: Optional[str] = "main"
    created_at: datetime = datetime.now(timezone.utc)
    updated_at: datetime = datetime.now(timezone.utc)

    @validator('title')
    def validate_title(cls, v):
        if not is_valid_title(v):
            raise ValueError('Invalid page title')
        return v

    @validator('content')
    def validate_content(cls, v):
        if v is None:
            return ""
        return v

    @validator('author')
    def validate_author(cls, v):
        if not v or not v.strip():
            return "Anonymous"
        return v.strip()

    @validator('branch')
    def validate_branch(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()


class PageUpdate(BaseModel):
    """Model for page update operations."""
    content: str
    author: Optional[str] = "Anonymous"

    @validator('content')
    def validate_content(cls, v):
        if v is None:
            return ""
        return v

    @validator('author')
    def validate_author(cls, v):
        if not v or not v.strip():
            return "Anonymous"
        return v.strip()


class PageSearch(BaseModel):
    """Model for search queries."""
    query: str
    branch: Optional[str] = "main"

    @validator('query')
    def validate_query(cls, v):
        if not v or not v.strip():
            raise ValueError('Search query cannot be empty')
        return v.strip()

    @validator('branch')
    def validate_branch(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()
