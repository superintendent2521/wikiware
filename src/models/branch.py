"""
Branch data models and validation for WikiWare.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime, timezone
from ..utils.validation import is_valid_title, is_valid_branch_name


class Branch(BaseModel):
    """Model for branch data."""

    page_title: str
    branch_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_from: Optional[str] = "main"

    @validator("page_title")
    def validate_page_title(cls, v):
        if not is_valid_title(v):
            raise ValueError("Invalid page title")
        return v

    @validator("branch_name")
    def validate_branch_name(cls, v):
        if not is_valid_branch_name(v):
            raise ValueError("Invalid branch name")
        return v

    @validator("created_from")
    def validate_created_from(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()


class BranchCreate(BaseModel):
    """Model for branch creation requests."""

    branch_name: str
    source_branch: Optional[str] = "main"

    @validator("branch_name")
    def validate_branch_name(cls, v):
        if not is_valid_branch_name(v):
            raise ValueError("Invalid branch name")
        return v

    @validator("source_branch")
    def validate_source_branch(cls, v):
        if not v or not v.strip():
            return "main"
        return v.strip()
