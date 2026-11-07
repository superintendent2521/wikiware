#!/usr/bin/env python3
"""One-shot, offline MongoDB to PostgreSQL data migration for WikiWare.

Run Alembic first, stop application writes, then run this script against an
empty PostgreSQL schema.  The import is a single transaction: any unexpected
failure leaves the target empty and safe to retry.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from src.schema import (
    analytics_events,
    branch_editors,
    favorites,
    image_hashes,
    page_branches,
    pages,
    revisions,
    sessions,
    settings,
    system_logs,
    user_page_edit_counts,
    users,
)

load_dotenv()


def _postgres_url() -> str:
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/wikiware",
    )
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _mongo_database_name(url: str) -> str:
    configured = os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB")
    if configured:
        return configured
    path = unquote(urlparse(url).path.lstrip("/"))
    return path.split("/", 1)[0] if path else "wikiware"


def _utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        normalized = _utc(value)
        return normalized.isoformat() if normalized else str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _with_timestamp(values: dict[str, Any], name: str, value: Any) -> None:
    normalized = _utc(value)
    if normalized is not None:
        values[name] = normalized


async def migrate() -> None:
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    mongo_client = MongoClient(mongo_url)
    mongo = mongo_client[_mongo_database_name(mongo_url)]
    engine = create_async_engine(_postgres_url(), pool_pre_ping=True)
    skipped: dict[str, int] = defaultdict(int)
    imported: dict[str, int] = defaultdict(int)

    try:
        async with engine.begin() as connection:
            for table in (
                users,
                pages,
                user_page_edit_counts,
                page_branches,
                revisions,
                favorites,
                branch_editors,
                sessions,
                image_hashes,
                analytics_events,
                system_logs,
                settings,
            ):
                count = await connection.scalar(select(func.count()).select_from(table))
                if count:
                    raise RuntimeError(
                        f"Target table '{table.name}' is not empty; use a fresh database"
                    )

            user_ids: dict[str, int] = {}
            mongo_user_ids: dict[str, int] = {}
            user_documents = list(mongo.users.find())
            for document in user_documents:
                username = document.get("username")
                password_hash = document.get("password_hash")
                if not username or not password_hash:
                    skipped["users"] += 1
                    continue
                values = {
                    "username": username,
                    "password_hash": password_hash,
                    "is_active": bool(document.get("is_active", True)),
                    "is_admin": bool(document.get("is_admin", False)),
                    "total_edits": int(document.get("total_edits", 0) or 0),
                }
                for name in (
                    "created_at",
                    "password_changed_at",
                    "last_collection_export_at",
                ):
                    _with_timestamp(values, name, document.get(name))
                user_id = await connection.scalar(insert(users).values(**values).returning(users.c.id))
                user_ids[username] = user_id
                mongo_user_ids[str(document.get("_id"))] = user_id
                imported["users"] += 1

            branch_metadata = {
                (
                    document.get("page_title") or document.get("title"),
                    document.get("branch_name") or document.get("branch"),
                ): document
                for document in mongo.branches.find()
            }
            page_ids: dict[str, int] = {}
            branch_ids: dict[tuple[str, str], int] = {}
            allowed_users: dict[int, list[str]] = {}
            page_documents = list(mongo.pages.find())
            for document in page_documents:
                title = document.get("title") or document.get("page_title")
                branch = document.get("branch") or document.get("branch_name") or "main"
                if not title:
                    skipped["page_branches"] += 1
                    continue
                if title not in page_ids:
                    page_values = {"title": title}
                    _with_timestamp(page_values, "created_at", document.get("created_at"))
                    page_ids[title] = await connection.scalar(
                        insert(pages).values(**page_values).returning(pages.c.id)
                    )
                    imported["pages"] += 1

                metadata = branch_metadata.get((title, branch), {})
                values = {
                    "page_id": page_ids[title],
                    "name": branch,
                    "created_from": metadata.get("created_from") or "main",
                    "content": document.get("content") or "",
                    "author_name": document.get("author") or "Anonymous",
                    "edit_summary": document.get("edit_summary") or "",
                    "edit_permission": document.get("edit_permission") or "everybody",
                }
                _with_timestamp(
                    values,
                    "created_at",
                    document.get("created_at") or metadata.get("created_at"),
                )
                _with_timestamp(values, "updated_at", document.get("updated_at"))
                branch_id = await connection.scalar(
                    insert(page_branches).values(**values).returning(page_branches.c.id)
                )
                branch_ids[(title, branch)] = branch_id
                allowed_users[branch_id] = document.get("allowed_users") or []
                imported["page_branches"] += 1

            for document in user_documents:
                user_id = user_ids.get(document.get("username"))
                if not user_id:
                    continue
                for title, edit_count in (document.get("page_edits") or {}).items():
                    page_id = page_ids.get(title)
                    if not page_id:
                        skipped["user_page_edit_counts"] += 1
                        continue
                    await connection.execute(
                        insert(user_page_edit_counts).values(
                            user_id=user_id,
                            page_id=page_id,
                            edit_count=int(edit_count or 0),
                        )
                    )
                    imported["user_page_edit_counts"] += 1

            history_by_branch: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for document in mongo.history.find():
                key = (document.get("title"), document.get("branch") or "main")
                if key not in branch_ids:
                    skipped["revisions"] += 1
                    continue
                history_by_branch[key].append(document)

            for key, documents in history_by_branch.items():
                documents.sort(
                    key=lambda item: (
                        _utc(item.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
                        str(item.get("_id", "")),
                    )
                )
                for revision_number, document in enumerate(documents, start=1):
                    values = {
                        "page_branch_id": branch_ids[key],
                        "revision_number": revision_number,
                        "content": document.get("content") or "",
                        "author_name": document.get("author") or "Anonymous",
                        "edited_by": document.get("edited_by")
                        or document.get("author")
                        or "Anonymous",
                        "edit_summary": document.get("edit_summary") or "",
                    }
                    _with_timestamp(values, "created_at", document.get("updated_at"))
                    await connection.execute(insert(revisions).values(**values))
                    imported["revisions"] += 1

            for branch_id, usernames in allowed_users.items():
                if isinstance(usernames, str):
                    usernames = [part.strip() for part in usernames.split(",")]
                for username in dict.fromkeys(usernames):
                    user_id = user_ids.get(username)
                    if not user_id:
                        skipped["branch_editors"] += 1
                        continue
                    await connection.execute(
                        insert(branch_editors).values(
                            page_branch_id=branch_id,
                            user_id=user_id,
                        )
                    )
                    imported["branch_editors"] += 1

            for document in user_documents:
                user_id = user_ids.get(document.get("username"))
                if not user_id:
                    continue
                normalized: set[tuple[str, str]] = set()
                for favorite in document.get("favorites") or []:
                    if isinstance(favorite, str):
                        key = (favorite, "main")
                    elif isinstance(favorite, dict) and favorite.get("title"):
                        key = (favorite["title"], favorite.get("branch") or "main")
                    else:
                        skipped["favorites"] += 1
                        continue
                    normalized.add(key)
                for key in normalized:
                    branch_id = branch_ids.get(key)
                    if not branch_id:
                        skipped["favorites"] += 1
                        continue
                    await connection.execute(
                        insert(favorites).values(user_id=user_id, page_branch_id=branch_id)
                    )
                    imported["favorites"] += 1

            for document in mongo.sessions.find():
                raw_user_id = str(document.get("user_id", ""))
                user_id = user_ids.get(raw_user_id) or mongo_user_ids.get(raw_user_id)
                expires_at = _utc(document.get("expires_at"))
                if not user_id or not document.get("session_id") or not expires_at:
                    skipped["sessions"] += 1
                    continue
                values = {
                    "session_id": document["session_id"],
                    "user_id": user_id,
                    "expires_at": expires_at,
                    "data": _json_value(document.get("data") or {}),
                }
                _with_timestamp(values, "created_at", document.get("created_at"))
                await connection.execute(insert(sessions).values(**values))
                imported["sessions"] += 1

            for document in mongo.image_hashes.find():
                if not document.get("filename") or not document.get("sha256"):
                    skipped["image_hashes"] += 1
                    continue
                values = {
                    "filename": document["filename"],
                    "sha256": document["sha256"],
                    "size": document.get("size"),
                    "url": document.get("url"),
                }
                _with_timestamp(values, "last_modified", document.get("last_modified"))
                _with_timestamp(values, "created_at", document.get("created_at"))
                await connection.execute(insert(image_hashes).values(**values))
                imported["image_hashes"] += 1

            for document in mongo.analytics_events.find():
                if not document.get("event_type"):
                    skipped["analytics_events"] += 1
                    continue
                values = {
                    "event_type": document["event_type"],
                    "page_title": document.get("page_title"),
                    "branch": document.get("branch"),
                    "referrer": document.get("referrer"),
                    "query": document.get("query"),
                    "query_normalized": document.get("query_normalized"),
                    "results": document.get("results"),
                }
                _with_timestamp(values, "timestamp", document.get("timestamp"))
                await connection.execute(insert(analytics_events).values(**values))
                imported["analytics_events"] += 1

            for document in mongo.system_logs.find():
                if not document.get("action"):
                    skipped["system_logs"] += 1
                    continue
                values = {
                    "username": document.get("username"),
                    "action": document["action"],
                    "message": document.get("message") or "",
                    "category": document.get("category") or "general",
                    "metadata": _json_value(document.get("metadata") or {}),
                }
                _with_timestamp(values, "timestamp", document.get("timestamp"))
                await connection.execute(insert(system_logs).values(**values))
                imported["system_logs"] += 1

            for document in mongo.settings.find():
                key = str(document.get("_id") or document.get("key") or "")
                if not key:
                    skipped["settings"] += 1
                    continue
                value = {
                    name: _json_value(item)
                    for name, item in document.items()
                    if name not in {"_id", "key"}
                }
                await connection.execute(insert(settings).values(key=key, value=value))
                imported["settings"] += 1
    finally:
        mongo_client.close()
        await engine.dispose()

    print("Migration committed.")
    for name in sorted(imported):
        print(f"  {name}: {imported[name]} imported")
    for name in sorted(skipped):
        print(f"  {name}: {skipped[name]} skipped")


if __name__ == "__main__":
    asyncio.run(migrate())
