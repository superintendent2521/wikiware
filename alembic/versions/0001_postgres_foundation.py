"""Create the initial WikiWare PostgreSQL schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_postgres_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True)),
        sa.Column("last_collection_export_at", sa.DateTime(timezone=True)),
        sa.Column("total_edits", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "user_page_edit_counts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edit_count", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("user_id", "page_id", name="uq_user_page_edit_counts_user_page"),
    )
    op.create_table(
        "page_branches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_from", sa.String(100), server_default="main", nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("author_name", sa.String(50), server_default="Anonymous", nullable=False),
        sa.Column("edit_summary", sa.String(250), server_default="", nullable=False),
        sa.Column("edit_permission", sa.String(30), server_default="everybody", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("page_id", "name", name="uq_page_branches_page_name"),
    )
    op.create_index("ix_page_branches_page_updated", "page_branches", ["page_id", "updated_at"])
    op.create_index("ix_page_branches_name_updated", "page_branches", ["name", "updated_at"])
    op.create_table(
        "revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_branch_id", sa.Integer(), sa.ForeignKey("page_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(50), server_default="Anonymous", nullable=False),
        sa.Column("edited_by", sa.String(50), server_default="Anonymous", nullable=False),
        sa.Column("edit_summary", sa.String(250), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("page_branch_id", "revision_number", name="uq_revisions_branch_number"),
    )
    op.create_index("ix_revisions_branch_created", "revisions", ["page_branch_id", "created_at"])
    op.create_table(
        "favorites",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_branch_id", sa.Integer(), sa.ForeignKey("page_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "page_branch_id", name="uq_favorites_user_branch"),
    )
    op.create_table(
        "branch_editors",
        sa.Column("page_branch_id", sa.Integer(), sa.ForeignKey("page_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("page_branch_id", "user_id", name="uq_branch_editors_branch_user"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table(
        "image_hashes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer()),
        sa.Column("last_modified", sa.DateTime(timezone=True)),
        sa.Column("url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_image_hashes_sha256", "image_hashes", ["sha256"])
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("page_title", sa.String(255)),
        sa.Column("branch", sa.String(100)),
        sa.Column("referrer", sa.Text()),
        sa.Column("query", sa.Text()),
        sa.Column("query_normalized", sa.Text()),
        sa.Column("results", sa.Integer()),
        sa.Column("metadata", postgresql.JSONB()),
    )
    op.create_index("ix_analytics_event_type_timestamp", "analytics_events", ["event_type", "timestamp"])
    op.create_index("ix_analytics_timestamp", "analytics_events", ["timestamp"])
    op.create_index("ix_analytics_query_normalized", "analytics_events", ["query_normalized"])
    op.create_table(
        "system_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), server_default="general", nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_system_logs_action_timestamp", "system_logs", ["action", "timestamp"])
    op.create_index("ix_system_logs_timestamp", "system_logs", ["timestamp"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for table_name in (
        "settings",
        "system_logs",
        "analytics_events",
        "image_hashes",
        "sessions",
        "branch_editors",
        "favorites",
        "revisions",
        "page_branches",
        "user_page_edit_counts",
        "pages",
        "users",
    ):
        op.drop_table(table_name)
