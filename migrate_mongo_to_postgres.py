"""
One-off migration script to copy data from a MongoDB instance into Postgres.

Usage:
  MONGO_URI="mongodb://localhost:27017" DATABASE_URL="postgresql://user:pass@localhost:5432/wikiware" python migrate_mongo_to_postgres.py

Environment variables:
  - MONGO_URI (required): connection string for the source MongoDB.
  - MONGO_DB (optional): database name in MongoDB (default: "wikiware").
  - DATABASE_URL or POSTGRES_DSN (required): Postgres DSN.
  - MONGO_BACKUP_BEFORE_MIGRATE (optional): if true, run mongodump before migration
    starts (default: "true").
  - MONGO_BACKUP_DIR (optional): output directory for backup archives.
  - MONGO_BACKUP_FILE (optional): backup archive filename (inside MONGO_BACKUP_DIR).

The script is idempotent where possible: it uses upserts/ON CONFLICT clauses to
avoid duplicate inserts and keeps existing rows when appropriate.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import ParseResult, urlparse, urlunparse

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
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
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


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: Optional[str] = None) -> Optional[str]:
    if value is None:
        return default
    text = str(value)
    if text == "":
        return default
    return text


def _json_compatible(value: Any) -> Any:
    """
    Return a JSON-serializable object.

    Mongo values can contain BSON types (ObjectId, Decimal128, etc.) that
    asyncpg cannot encode to json/jsonb directly.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


def _collection(db, name: str):
    """Return a collection if it exists; otherwise None."""
    if name in db.list_collection_names():
        return db[name]
    return None


def _collection_count(col) -> int:
    """Return a best-effort count for progress logs."""
    try:
        return int(col.count_documents({}))
    except Exception:
        return 0


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _uri_with_db(uri: str, db_name: str) -> str:
    """Force a Mongo URI path to point at the selected database."""
    parsed = urlparse(uri)
    forced = ParseResult(
        scheme=parsed.scheme,
        netloc=parsed.netloc,
        path=f"/{db_name}",
        params=parsed.params,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return urlunparse(forced)


def _run_mongo_backup(mongo_uri: str, mongo_db_name: str) -> Path:
    """
    Create a Mongo archive backup and return its path.

    Raises SystemExit if backup cannot be created.
    """
    if shutil.which("mongodump") is None:
        raise SystemExit(
            "mongodump was not found on PATH. Install MongoDB Database Tools "
            "or set MONGO_BACKUP_BEFORE_MIGRATE=0 to skip backup."
        )

    backup_dir = Path(
        os.getenv("MONGO_BACKUP_DIR") or Path(__file__).resolve().parent / "backups"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    safe_db_name = "".join(
        ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in mongo_db_name
    )
    backup_name = os.getenv("MONGO_BACKUP_FILE")
    if backup_name:
        archive_path = backup_dir / backup_name
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        archive_path = backup_dir / f"{safe_db_name}-{stamp}.archive.gz"

    if archive_path.exists():
        raise SystemExit(
            f"Refusing to overwrite existing backup archive: {archive_path}. "
            "Set MONGO_BACKUP_FILE to a new filename."
        )

    cmd = [
        "mongodump",
        f"--uri={_uri_with_db(mongo_uri, mongo_db_name)}",
        f"--archive={archive_path}",
        "--gzip",
    ]
    print(f"Creating Mongo backup: {archive_path}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Mongo backup failed with exit code {exc.returncode}. "
            "Aborting migration before writing to Postgres."
        ) from exc

    print("Mongo backup complete.")
    return archive_path


def _safe_postgres_dsn(dsn: str) -> str:
    # Hide password while still showing host/db in logs.
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    creds, tail = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{tail}"
    return f"{scheme}://{creds}@{tail}"


async def migrate_users(conn: asyncpg.Connection, db) -> None:
    col = _collection(db, "users")
    if col is None:
        print("Skipping users: collection not found")
        return
    count = _collection_count(col)
    print(f"Migrating users ({count} found)...")
    for doc in col.find({}):
        username = _as_str(doc.get("username"))
        if not username:
            continue
        password_hash = _as_str(doc.get("password_hash") or doc.get("password"), "")
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        is_active = bool(doc.get("is_active", True))
        is_admin = bool(doc.get("is_admin", False))
        password_changed_at = _as_utc(doc.get("password_changed_at"))
        last_collection_export_at = _as_utc(doc.get("last_collection_export_at"))
        page_edits = _json_compatible(doc.get("page_edits") or {})
        total_edits = _as_int(doc.get("total_edits"), 0) or 0
        favorites = _json_compatible(doc.get("favorites") or [])
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
    count = _collection_count(col)
    print(f"Migrating pages ({count} found)...")
    for doc in col.find({}):
        title = _as_str(doc.get("title"))
        if not title:
            continue
        branch = _as_str(doc.get("branch"), "main")
        content = _as_str(doc.get("content"), "")
        author = _as_str(doc.get("author"), "Anonymous")
        edit_summary = _as_str(doc.get("edit_summary"))
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        updated_at = _as_utc(doc.get("updated_at")) or created_at
        edit_permission = _as_str(doc.get("edit_permission"), "everybody")
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
    count = _collection_count(col)
    print(f"Migrating history ({count} found)...")
    for doc in col.find({}):
        title = _as_str(doc.get("title"))
        content = _as_str(doc.get("content"), "")
        if not title or not content:
            continue
        author = _as_str(doc.get("author"), "Anonymous")
        edited_by = _as_str(doc.get("edited_by"))
        branch = _as_str(doc.get("branch"), "main")
        updated_at = _as_utc(doc.get("updated_at")) or datetime.now(timezone.utc)
        edit_summary = _as_str(doc.get("edit_summary"))
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
    count = _collection_count(col)
    print(f"Migrating branches ({count} found)...")
    for doc in col.find({}):
        page_title = _as_str(doc.get("page_title") or doc.get("title"))
        branch_name = _as_str(doc.get("branch_name"))
        if not page_title or not branch_name:
            continue
        created_at = _as_utc(doc.get("created_at")) or datetime.now(timezone.utc)
        created_from = _as_str(doc.get("created_from"))
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
    count = _collection_count(col)
    print(f"Migrating sessions ({count} found)...")
    for doc in col.find({}):
        session_id = _as_str(doc.get("session_id") or doc.get("_id"))
        user_id = _as_str(doc.get("user_id") or doc.get("username"))
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
    count = _collection_count(col)
    print(f"Migrating image_hashes ({count} found)...")
    for doc in col.find({}):
        filename = _as_str(doc.get("filename"))
        sha256 = _as_str(doc.get("sha256"))
        if not filename or not sha256:
            continue
        size = _as_int(doc.get("size"))
        modified = _as_utc(doc.get("modified"))
        url = _as_str(doc.get("url"))
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
    count = _collection_count(col)
    print(f"Migrating settings ({count} found)...")
    for doc in col.find({}):
        key = _as_str(doc.get("key") or doc.get("_id"))
        if not key:
            continue
        data = doc.get("data") or {k: v for k, v in doc.items() if k not in {"_id", "key"}}
        data = _json_compatible(data)
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
    count = _collection_count(col)
    print(f"Migrating system_logs ({count} found)...")
    for doc in col.find({}):
        username = _as_str(doc.get("username"))
        action = _as_str(doc.get("action") or doc.get("event"), "unknown")
        message = _as_str(doc.get("message"), "")
        category = _as_str(doc.get("category"), "general")
        metadata = _json_compatible(doc.get("metadata") or {})
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
    count = _collection_count(col)
    print(f"Migrating analytics_events ({count} found)...")
    for doc in col.find({}):
        event_type = _as_str(doc.get("event_type") or doc.get("type"))
        if not event_type:
            continue
        timestamp = _as_utc(doc.get("timestamp")) or datetime.now(timezone.utc)
        page_title = _as_str(doc.get("page_title"))
        branch = _as_str(doc.get("branch"))
        referrer = _as_str(doc.get("referrer"))
        query = _as_str(doc.get("query"))
        query_normalized = _as_str(doc.get("query_normalized"))
        results = _as_int(doc.get("results"), 0) or 0
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
    backup_before_migrate = _env_flag("MONGO_BACKUP_BEFORE_MIGRATE", default=True)

    if not mongo_uri:
        raise SystemExit("MONGO_URI is required (source MongoDB connection string).")
    if not pg_dsn:
        raise SystemExit("DATABASE_URL or POSTGRES_DSN is required (target Postgres DSN).")

    if backup_before_migrate:
        _run_mongo_backup(mongo_uri, mongo_db_name)
    else:
        print("Skipping Mongo backup (MONGO_BACKUP_BEFORE_MIGRATE disabled).")

    mongo_client = MongoClient(mongo_uri)
    mongo_db = mongo_client[mongo_db_name]

    print(f"Connecting to Postgres at {_safe_postgres_dsn(pg_dsn)} ...")
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
