"""PostgreSQL schema shared by the application and Alembic."""

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Table,
    Column,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB


metadata = MetaData()
json_type = JSONB().with_variant(JSON(), "sqlite")
timestamp = DateTime(timezone=True)


users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(50), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("is_admin", Boolean, nullable=False, server_default="false"),
    Column("password_changed_at", timestamp),
    Column("last_collection_export_at", timestamp),
    Column("total_edits", Integer, nullable=False, server_default="0"),
    Index("ix_users_created_at", "created_at"),
)

pages = Table(
    "pages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(255), nullable=False, unique=True),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
)

user_page_edit_counts = Table(
    "user_page_edit_counts",
    metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("page_id", ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
    Column("edit_count", Integer, nullable=False, server_default="0"),
    UniqueConstraint("user_id", "page_id", name="uq_user_page_edit_counts_user_page"),
)

page_branches = Table(
    "page_branches",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("page_id", ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(100), nullable=False),
    Column("created_from", String(100), nullable=False, server_default="main"),
    Column("content", Text, nullable=False, server_default=""),
    Column("author_name", String(50), nullable=False, server_default="Anonymous"),
    Column("edit_summary", String(250), nullable=False, server_default=""),
    Column("edit_permission", String(30), nullable=False, server_default="everybody"),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    Column("updated_at", timestamp, nullable=False, server_default=func.now()),
    UniqueConstraint("page_id", "name", name="uq_page_branches_page_name"),
    Index("ix_page_branches_page_updated", "page_id", "updated_at"),
    Index("ix_page_branches_name_updated", "name", "updated_at"),
)

revisions = Table(
    "revisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "page_branch_id",
        ForeignKey("page_branches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("revision_number", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("author_name", String(50), nullable=False, server_default="Anonymous"),
    Column("edited_by", String(50), nullable=False, server_default="Anonymous"),
    Column("edit_summary", String(250), nullable=False, server_default=""),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    UniqueConstraint(
        "page_branch_id", "revision_number", name="uq_revisions_branch_number"
    ),
    Index("ix_revisions_branch_created", "page_branch_id", "created_at"),
)

favorites = Table(
    "favorites",
    metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column(
        "page_branch_id",
        ForeignKey("page_branches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    UniqueConstraint("user_id", "page_branch_id", name="uq_favorites_user_branch"),
)

branch_editors = Table(
    "branch_editors",
    metadata,
    Column("page_branch_id", ForeignKey("page_branches.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    UniqueConstraint("page_branch_id", "user_id", name="uq_branch_editors_branch_user"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", String(255), nullable=False, unique=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("data", json_type, nullable=False, server_default="{}"),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    Column("expires_at", timestamp, nullable=False),
    Index("ix_sessions_user_id", "user_id"),
    Index("ix_sessions_expires_at", "expires_at"),
)

image_hashes = Table(
    "image_hashes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("filename", String(512), nullable=False, unique=True),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer),
    Column("last_modified", timestamp),
    Column("url", Text),
    Column("created_at", timestamp, nullable=False, server_default=func.now()),
    Index("ix_image_hashes_sha256", "sha256"),
)

analytics_events = Table(
    "analytics_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_type", String(50), nullable=False),
    Column("timestamp", timestamp, nullable=False, server_default=func.now()),
    Column("page_title", String(255)),
    Column("branch", String(100)),
    Column("referrer", Text),
    Column("query", Text),
    Column("query_normalized", Text),
    Column("results", Integer),
    Column("metadata", json_type),
    Index("ix_analytics_event_type_timestamp", "event_type", "timestamp"),
    Index("ix_analytics_timestamp", "timestamp"),
    Index("ix_analytics_query_normalized", "query_normalized"),
)

system_logs = Table(
    "system_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(50)),
    Column("action", String(100), nullable=False),
    Column("message", Text, nullable=False),
    Column("category", String(50), nullable=False, server_default="general"),
    Column("metadata", json_type, nullable=False, server_default="{}"),
    Column("timestamp", timestamp, nullable=False, server_default=func.now()),
    Index("ix_system_logs_action_timestamp", "action", "timestamp"),
    Index("ix_system_logs_timestamp", "timestamp"),
)

settings = Table(
    "settings",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", json_type, nullable=False, server_default="{}"),
    Column("updated_at", timestamp, nullable=False, server_default=func.now()),
)


__all__ = [
    "metadata",
    "users",
    "pages",
    "user_page_edit_counts",
    "page_branches",
    "revisions",
    "favorites",
    "branch_editors",
    "sessions",
    "image_hashes",
    "analytics_events",
    "system_logs",
    "settings",
]
