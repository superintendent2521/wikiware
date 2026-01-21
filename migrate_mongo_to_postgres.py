"""
One-off migration script to copy data from a MongoDB instance into Postgres.

Usage:
  MONGO_URI="mongodb://localhost:27017" DATABASE_URL="postgresql://user:pass@localhost:5432/wikiware" python migrate_mongo_to_postgres.py

Environment variables:
  - MONGO_URI (required): connection string for the source MongoDB.
  - MONGO_DB (optional): database name in MongoDB (default: "wikiware").
  - DATABASE_URL or POSTGRES_DSN (required): Postgres DSN.

The script is idempotent where possible: it uses upserts/ON CONFLICT clauses to
avoid duplicate inserts and keeps existing rows when appropriate.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import asyncpg
from pymongo import MongoClient


def _as_utc(value: Any) -> Optional[datetime]:
    """Return a timezone-aware datetime (UTC) or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _as_list(value: Any) -> List[str]:
    """Normalize allowed_users/favorites values to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _collection(db, name: str):
    """Return a collection if it exists; otherwise None."""
    if name in db.list_collection_names():
        return db[name]
    return None


async def migrate_users(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "users")
    if col is None:
        print("Skipping users: collection not found")
        return
    users = list(col.find({}))
    print(f"Migrating users ({len(users)} found)...")
    for doc in users:
        username = doc.get("username")
        if not username:
            continue
        password_hash = doc.get("password_hash") or doc.get("password") or ""
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        is_active = bool(doc.get("is_active", True))
        is_admin = bool(doc.get("is_admin", False))
        password_changed_at = _as_utc(doc.get("password_changed_at"))
        last_collection_export_at = _as_utc(doc.get("last_collection_export_at"))
        page_edits = doc.get("page_edits") or {}
        total_edits = int(doc.get("total_edits", 0) or 0)
        favorites = doc.get("favorites") or []
        await conn.execute(
            """
            INSERT INTO users (
                username, password_hash, created_at, is_active, is_admin,
                password_changed_at, last_collection_export_at, page_edits, total_edits, favorites
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                is_active = EXCLUDED.is_active,
                is_admin = EXCLUDED.is_admin,
                password_changed_at = EXCLUDED.password_changed_at,
                last_collection_export_at = EXCLUDED.last_collection_export_at,
                page_edits = EXCLUDED.page_edits,
                total_edits = EXCLUDED.total_edits,
                favorites = EXCLUDED.favorites
            """,
            username,
            password_hash,
            created_at,
            is_active,
            is_admin,
            password_changed_at,
            last_collection_export_at,
            page_edits,
            total_edits,
            favorites,
        )
    print("Users migrated.")


async def migrate_pages(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "pages")
    if col is None:
        print("Skipping pages: collection not found")
        return
    pages = list(col.find({}))
    print(f"Migrating pages ({len(pages)} found)...")
    for doc in pages:
        title = doc.get("title")
        if not title:
            continue
        branch = doc.get("branch") or "main"
        content = doc.get("content") or ""
        author = doc.get("author") or "Anonymous"
        edit_summary = doc.get("edit_summary")
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        updated_at = _as_utc(doc.get("updated_at")) or created_at
        edit_permission = doc.get("edit_permission") or "everybody"
        allowed_users = _as_list(doc.get("allowed_users"))
        await conn.execute(
            """
            INSERT INTO pages (
                title, branch, content, author, edit_summary,
                created_at, updated_at, edit_permission, allowed_users
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (title, branch) DO UPDATE SET
                content = EXCLUDED.content,
                author = EXCLUDED.author,
                edit_summary = EXCLUDED.edit_summary,
                updated_at = EXCLUDED.updated_at,
                edit_permission = EXCLUDED.edit_permission,
                allowed_users = EXCLUDED.allowed_users
            """,
            title,
            branch,
            content,
            author,
            edit_summary,
            created_at,
            updated_at,
            edit_permission,
            allowed_users,
        )
    print("Pages migrated.")


async def migrate_history(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "history")
    if col is None:
        print("Skipping history: collection not found")
        return
    history_entries = list(col.find({}))
    print(f"Migrating history ({len(history_entries)} found)...")
    for doc in history_entries:
        title = doc.get("title")
        content = doc.get("content") or ""
        if not title or not content:
            continue
        author = doc.get("author") or "Anonymous"
        edited_by = doc.get("edited_by")
        branch = doc.get("branch") or "main"
        updated_at = _as_utc(doc.get("updated_at")) or datetime.now(timezone.utc)
        edit_summary = doc.get("edit_summary")
        await conn.execute(
            """
            INSERT INTO history (
                title, content, author, edited_by, branch, updated_at, edit_summary
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            title,
            content,
            author,
            edited_by,
            branch,
            updated_at,
            edit_summary,
        )
    print("History migrated.")


async def migrate_branches(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "branches")
    if col is None:
        print("Skipping branches: collection not found")
        return
    branches = list(col.find({}))
    print(f"Migrating branches ({len(branches)} found)...")
    for doc in branches:
        page_title = doc.get("page_title") or doc.get("title")
        branch_name = doc.get("branch_name")
        if not page_title or not branch_name:
            continue
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        created_from = doc.get("created_from")
        await conn.execute(
            """
            INSERT INTO branches (
                page_title, branch_name, created_at, created_from
            )
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (page_title, branch_name) DO NOTHING
            """,
            page_title,
            branch_name,
            created_at,
            created_from,
        )
    print("Branches migrated.")


async def migrate_sessions(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "sessions")
    if col is None:
        print("Skipping sessions: collection not found")
        return
    sessions = list(col.find({}))
    print(f"Migrating sessions ({len(sessions)} found)...")
    for doc in sessions:
        session_id = doc.get("session_id") or doc.get("_id")
        user_id = doc.get("user_id") or doc.get("username")
        if not session_id or not user_id:
            continue
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        expires_at = _as_utc(doc.get("expires_at")) or created_at
        await conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (session_id) DO NOTHING
            """,
            session_id,
            user_id,
            created_at,
            expires_at,
        )
    print("Sessions migrated.")


async def migrate_image_hashes(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "image_hashes")
    if col is None:
        print("Skipping image_hashes: collection not found")
        return
    hashes = list(col.find({}))
    print(f"Migrating image_hashes ({len(hashes)} found)...")
    for doc in hashes:
        filename = doc.get("filename")
        sha256 = doc.get("sha256")
        if not filename or not sha256:
            continue
        size = doc.get("size")
        modified = _as_utc(doc.get("modified"))
        url = doc.get("url")
        await conn.execute(
            """
            INSERT INTO image_hashes (filename, sha256, size, modified, url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (sha256) DO UPDATE SET
                filename = EXCLUDED.filename,
                size = EXCLUDED.size,
                modified = EXCLUDED.modified,
                url = COALESCE(EXCLUDED.url, image_hashes.url)
            """,
            filename,
            sha256,
            size,
            modified,
            url,
        )
    print("Image hashes migrated.")


async def migrate_settings(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "settings")
    if col is None:
        print("Skipping settings: collection not found")
        return
    settings_docs = list(col.find({}))
    print(f"Migrating settings ({len(settings_docs)} found)...")
    for doc in settings_docs:
        key = doc.get("key") or doc.get("_id")
        if not key:
            continue
        data = doc.get("data") or {k: v for k, v in doc.items() if k not in {"_id", "key"}}
        await conn.execute(
            """
            INSERT INTO settings (key, data)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (key) DO UPDATE SET
                data = EXCLUDED.data,
                updated_at = now()
            """,
            key,
            data,
        )
    print("Settings migrated.")


async def migrate_system_logs(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "system_logs")
    if col is None:
        print("Skipping system_logs: collection not found")
        return
    logs = list(col.find({}))
    print(f"Migrating system_logs ({len(logs)} found)...")
    for doc in logs:
        username = doc.get("username")
        action = doc.get("action") or doc.get("event") or "unknown"
        message = doc.get("message") or ""
        category = doc.get("category") or "general"
        metadata = doc.get("metadata") or {}
        timestamp = _as_utc(doc.get("timestamp")) or datetime.now(timezone.utc)
        await conn.execute(
            """
            INSERT INTO system_logs (username, action, message, category, metadata, timestamp)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            username,
            action,
            message,
            category,
            metadata,
            timestamp,
        )
    print("System logs migrated.")


async def migrate_analytics(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "analytics_events")
    if col is None:
        print("Skipping analytics_events: collection not found")
        return
    events = list(col.find({}))
    print(f"Migrating analytics_events ({len(events)} found)...")
    for doc in events:
        event_type = doc.get("event_type") or doc.get("type")
        if not event_type:
            continue
        timestamp = _as_utc(doc.get("timestamp")) or datetime.now(timezone.utc)
        page_title = doc.get("page_title")
        branch = doc.get("branch")
        referrer = doc.get("referrer")
        query = doc.get("query")
        query_normalized = doc.get("query_normalized")
        results = int(doc.get("results", 0) or 0)
        await conn.execute(
            """
            INSERT INTO analytics_events (
                event_type, timestamp, page_title, branch, referrer, query, query_normalized, results
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            event_type,
            timestamp,
            page_title,
            branch,
            referrer,
            query,
            query_normalized,
            results,
        )
    print("Analytics events migrated.")


async def run() -> None:
    mongo_uri = os.getenv("MONGO_URI")
    pg_dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN")
    mongo_db_name = os.getenv("MONGO_DB", "wikiware")

    if not mongo_uri:
        raise SystemExit("MONGO_URI is required (source MongoDB connection string).")
    if not pg_dsn:
        raise SystemExit("DATABASE_URL or POSTGRES_DSN is required (target Postgres DSN).")

    mongo_client = MongoClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    print(f"Connecting to Postgres at {pg_dsn} ...")
    conn: Optional[asyncpg.Connection] = None
    try:
        conn = await asyncpg.connect(dsn=pg_dsn)
        print("Connected to Postgres. Starting migration...")
        await migrate_users(conn, mongo_db)
        await migrate_pages(conn, mongo_db)
        await migrate_history(conn, mongo_db)
        await migrate_branches(conn, mongo_db)
        await migrate_sessions(conn, mongo_db)
        await migrate_image_hashes(conn, mongo_db)
        await migrate_settings(conn, mongo_db)
        await migrate_system_logs(conn, mongo_db)
        await migrate_analytics(conn, mongo_db)
        print("Migration complete.")
    finally:
        if conn is not None:
            await conn.close()
        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(run())
