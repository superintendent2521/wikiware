"""
Page data models and validation for WikiWare.
"""

from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator
from ..utils.validation import is_valid_title


class WikiPage(BaseModel):
    """Model for wiki page data."""

    title: str
    content: str
    author: Optional[str] = "Anonymous"
    branch: Optional[str] = "main"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    edit_permission: str = "everybody"
    allowed_users: List[str] = Field(default_factory=list)

    @validator("title")
    def validate_title(cls, v):
        if not is_valid_title(v):
            raise ValueError("Invalid page title")
        return v

    @validator("content")
    def validate_content(cls, v):
        if v is None:
            return ""
        return v

    @validator("author")
    def validate_author(cls, v):
        if not v or not v.strip():
            return "Anonymous"
        return v.strip()

    @validator("branch")
    def validate_branch(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()


class PageUpdate(BaseModel):
    """Model for page update operations."""

    content: str
    author: Optional[str] = "Anonymous"

    @validator("content")
    def validate_content(cls, v):
        if v is None:
            return ""
        return v

    @validator("author")
    def validate_author(cls, v):
        if not v or not v.strip():
            return "Anonymous"
        return v.strip()


class PageSearch(BaseModel):
    """Model for search queries."""

    query: str
    branch: Optional[str] = "main"

    @validator("query")
    def validate_query(cls, v):
        if not v or not v.strip():
            raise ValueError("Search query cannot be empty")
        return v.strip()

    @validator("branch")
    def validate_branch(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()
