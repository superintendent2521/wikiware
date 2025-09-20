"""
User data models and validation for WikiWare.
"""

from pydantic import BaseModel, Field, validator
from datetime import datetime, timezone
import re


class User(BaseModel):
    """Model for user data."""

    username: str
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    is_admin: bool = False
    page_edits: dict = Field(default_factory=dict)  # Dictionary to track edits per page: {"page_title": edit_count}
    total_edits: int = 0  # Total number of edits by this user

    @validator("username")
    def validate_username(cls, v):
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters long")
        if len(v) > 50:
            raise ValueError("Username must be less than 50 characters")
        if not re.match("^[a-zA-Z0-9_]+$", v):
            raise ValueError(
                "Username can only contain letters, numbers, and underscores"
            )
        return v.strip()

    @validator("password_hash")
    def validate_password_hash(cls, v):
        if not v or not v.strip():
            raise ValueError("Password hash cannot be empty")
        return v


class UserRegistration(BaseModel):
    """Model for user registration."""

    username: str
    password: str

    @validator("username")
    def validate_username(cls, v):
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters long")
        if len(v) > 50:
            raise ValueError("Username must be less than 50 characters")
        if not re.match("^[a-zA-Z0-9_]+$", v):
            raise ValueError(
                "Username can only contain letters, numbers, and underscores"
            )
        return v.strip()

    @validator("password")
    def validate_password(cls, v):
        if not v or not v.strip():
            raise ValueError("Password cannot be empty")
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters long")
        return v


class UserLogin(BaseModel):
    """Model for user login."""

    username: str
    password: str

    @validator("username")
    def validate_username(cls, v):
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")
        return v.strip()

    @validator("password")
    def validate_password(cls, v):
        if not v or not v.strip():
            raise ValueError("Password cannot be empty")
        return v
