"""Database connection and management for PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncpg
from asyncpg import Record
from dotenv import load_dotenv
from loguru import logger

from . import config

load_dotenv()

DB_OPERATION_LOG_THRESHOLD_MS = (
    float(os.getenv("DB_OPERATION_LOG_THRESHOLD_MS", "100")) / 1000
)  # seconds


def _sanitize_dsn(dsn: str) -> str:
    """Strip secrets from a DSN for logging."""
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        user = f"{parsed.username}@" if parsed.username else ""
        netloc = f"{user}{host}{port}"
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
    except Exception:
        return dsn


def _record_to_dict(record: Optional[Record]) -> Optional[Dict[str, Any]]:
    """Convert an asyncpg Record into a plain dict."""
    if record is None:
        return None
    return dict(record)


class InsertOneResult:
    """Lightweight insert result with inserted_id."""

    def __init__(self, inserted_id: Any):
        self.inserted_id = inserted_id


class UpdateResult:
    """Lightweight update result with matched/modified counts."""

    def __init__(self, matched_count: int, modified_count: int):
        self.matched_count = matched_count
        self.modified_count = modified_count


class DeleteResult:
    """Lightweight delete result with deleted_count."""

    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class Cursor:
    """Simple cursor supporting sort/limit/skip and async iteration."""

    def __init__(
        self,
        fetcher: Callable[
            [List[Tuple[str, str]], Optional[int], int],
            Awaitable[List[Dict[str, Any]]],
        ],
    ):
        self._fetcher = fetcher
        self._sort: List[Tuple[str, str]] = []
        self._limit: Optional[int] = None
        self._skip: int = 0
        self._buffer: Optional[List[Dict[str, Any]]] = None
        self._iter_index = 0

    def sort(self, key: Any, direction: Optional[int] = None) -> "Cursor":
        """Capture sort ordering."""
        if isinstance(key, list):
            for item in key:
                if isinstance(item, tuple) or isinstance(item, list):
                    field, dir_val = item
                    dir_str = "DESC" if dir_val in (-1, "desc", "DESC") else "ASC"
                    self._sort.append((str(field), dir_str))
        else:
            dir_str = "DESC" if direction in (-1, "desc", "DESC") else "ASC"
            self._sort.append((str(key), dir_str))
        return self

    def limit(self, value: int) -> "Cursor":
        self._limit = int(value)
        return self

    def skip(self, value: int) -> "Cursor":
        self._skip = int(value)
        return self

    def collation(self, *_args, **_kwargs) -> "Cursor":  # compatibility no-op
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = self._limit
        if length is not None:
            limit = min(limit, length) if limit is not None else length
        return await self._fetcher(self._sort, limit, self._skip)

    def __aiter__(self):
        self._iter_index = 0
        self._buffer = None
        return self

    async def __anext__(self):
        if self._buffer is None:
            self._buffer = await self.to_list(self._limit)
        if self._iter_index >= len(self._buffer):
            raise StopAsyncIteration
        item = self._buffer[self._iter_index]
        self._iter_index += 1
        return item


class Database:
    """Manage PostgreSQL connections and migrations."""

    def __init__(
        self,
        dsn: str = config.DATABASE_URL,
        min_size: int = config.DB_POOL_MIN_CONNECTIONS,
        max_size: int = config.DB_POOL_MAX_CONNECTIONS,
    ):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self.is_connected = False
        self._connection_lock = asyncio.Lock()

    async def connect(self, max_retries: Optional[int] = 5) -> None:
        """Establish a connection pool with retry logic."""
        retry = 0
        delay = 2
        safe_dsn = _sanitize_dsn(self._dsn)
        async with self._connection_lock:
            while True:
                try:
                    logger.info(
                        "Connecting to Postgres at {} (attempt {}) with pool {}-{}",
                        safe_dsn,
                        retry + 1,
                        self._min_size,
                        self._max_size,
                    )
                    self._pool = await asyncpg.create_pool(
                        dsn=self._dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                    )
                    self.is_connected = True
                    await self._run_migrations()
                    logger.info("Connected to Postgres")
                    return
                except Exception as exc:
                    self._reset_state()
                    retry += 1
                    if max_retries is not None and retry >= max_retries:
                        message = (
                            "Failed to connect to Postgres. "
                            f"Tried {retry} time(s) using {safe_dsn}. "
                            "Ensure the database is running, host/port are reachable, "
                            "and credentials in DATABASE_URL are correct."
                        )
                        logger.error("{} Original error: {}", message, exc)
                        raise RuntimeError(f"{message} (original error: {exc})") from exc
                    logger.warning(
                        "Postgres connection failed (attempt {} of {}): {}. DSN={} retrying in {}s",
                        retry,
                        max_retries if max_retries is not None else "unlimited",
                        exc,
                        safe_dsn,
                        delay,
                    )
                    await asyncio.sleep(delay)

    async def _run_migrations(self) -> None:
        """Apply SQL migrations from the migrations directory."""
        migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            applied_rows = await conn.fetch("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in applied_rows}

            pending_files = sorted(migrations_dir.glob("*.sql"))
            applied_now: List[str] = []
            for path in pending_files:
                version = path.stem
                if version in applied:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", version
                    )
                applied_now.append(version)
            if applied_now:
                logger.info("Applied migrations: {}", ", ".join(applied_now))

    def _reset_state(self) -> None:
        if self._pool is not None:
            try:
                self._pool.terminate()
            except Exception:
                pass
        self._pool = None
        self.is_connected = False

    async def disconnect(self) -> None:
        """Close the pool."""
        async with self._connection_lock:
            if self._pool:
                await self._pool.close()
            self._reset_state()

    async def fetch(self, query: str, *args) -> List[Record]:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        start = time.monotonic()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        duration = time.monotonic() - start
        if config.DB_QUERY_LOGGING_ENABLED and duration >= DB_OPERATION_LOG_THRESHOLD_MS:
            logger.info("DB fetch took {:.4f}s: {}", duration, query)
        return rows

    async def fetchrow(self, query: str, *args) -> Optional[Record]:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args) -> Any:
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        return list(row.values())[0]

    async def execute(self, query: str, *args) -> str:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        start = time.monotonic()
        async with self._pool.acquire() as conn:
            status = await conn.execute(query, *args)
        duration = time.monotonic() - start
        if config.DB_QUERY_LOGGING_ENABLED and duration >= DB_OPERATION_LOG_THRESHOLD_MS:
            logger.info("DB execute took {:.4f}s: {}", duration, query)
        return status

    @asynccontextmanager
    async def transaction(self):
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    # Collection accessors
    def get_collection(self, name: str):
        name = name.lower()
        if name == "pages":
            return PagesCollection(self)
        if name == "history":
            return HistoryCollection(self)
        if name == "branches":
            return BranchesCollection(self)
        if name == "users":
            return UsersCollection(self)
        if name == "sessions":
            return SessionsCollection(self)
        if name == "image_hashes":
            return ImageHashesCollection(self)
        if name == "settings":
            return SettingsCollection(self)
        if name == "system_logs":
            return SystemLogsCollection(self)
        if name == "analytics_events":
            return AnalyticsEventsCollection(self)
        return None


def _build_simple_where(
    filter_query: Optional[Dict[str, Any]],
    *,
    key_map: Optional[Dict[str, str]] = None,
    allow_or: bool = False,
    start_index: int = 0,
) -> Tuple[str, List[Any]]:
    """Build a WHERE clause for simple equality filters."""
    if not filter_query:
        return "", []
    params: List[Any] = []

    def normalize(key: str) -> str:
        if key_map and key in key_map:
            return key_map[key]
        return key

    def handle(node: Dict[str, Any]) -> str:
        if "$and" in node:
            parts = [handle(part) for part in node["$and"]]
            return "(" + " AND ".join(parts) + ")"
        if allow_or and "$or" in node:
            parts = [handle(part) for part in node["$or"]]
            return "(" + " OR ".join(parts) + ")"
        clauses: List[str] = []
        for raw_key, value in node.items():
            key = normalize(raw_key)
            params.append(value)
            clauses.append(f"{key} = ${start_index + len(params)}")
        return "(" + " AND ".join(clauses) + ")"

    clause = handle(filter_query)
    return f"WHERE {clause}", params


class PagesCollection:
    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    def _key_map() -> Dict[str, str]:
        return {"branch_name": "branch", "page_title": "title"}

    def _where(
        self, filter_query: Optional[Dict[str, Any]], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(
            filter_query, key_map=self._key_map(), allow_or=True, start_index=start_index
        )

    async def find_one(self, filter_query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM pages {where} ORDER BY updated_at DESC LIMIT 1", *params
        )
        return _record_to_dict(row)

    def find(
        self, filter_query: Optional[Dict[str, Any]] = None, projection: Any = None
    ) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                order_parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(order_parts)
            limit_clause = ""
            if limit is not None:
                limit_clause = f" LIMIT {limit}"
            offset_clause = ""
            if skip:
                offset_clause = f" OFFSET {skip}"
            rows = await self._db.fetch(
                f"SELECT * FROM pages {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            return [_record_to_dict(r) for r in rows if r is not None]

        return Cursor(_fetch)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO pages ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)

    async def update_one(
        self, filter_query: Dict[str, Any], update: Dict[str, Any]
    ) -> UpdateResult:
        existing = await self.find_one(filter_query)
        if not existing:
            return UpdateResult(0, 0)
        set_data = update.get("$set", {}) if "$set" in update else update
        if not set_data:
            return UpdateResult(1, 0)

        params: List[Any] = []
        assignments: List[str] = []
        for key, value in set_data.items():
            assignments.append(f"{key} = ${len(params)+1}")
            params.append(value)

        where, where_params = self._where(filter_query, start_index=len(params))
        params.extend(where_params)
        await self._db.execute(
            f"UPDATE pages SET {', '.join(assignments)} {where}", *params
        )
        return UpdateResult(1, 1)

    async def update_many(
        self, filter_query: Dict[str, Any], update: Dict[str, Any]
    ) -> UpdateResult:
        set_data = update.get("$set", {}) if "$set" in update else update
        if not set_data:
            return UpdateResult(0, 0)
        params: List[Any] = []
        assignments: List[str] = []
        for key, value in set_data.items():
            assignments.append(f"{key} = ${len(params)+1}")
            params.append(value)
        where, where_params = self._where(filter_query, start_index=len(params))
        params.extend(where_params)
        status = await self._db.execute(
            f"UPDATE pages SET {', '.join(assignments)} {where}", *params
        )
        matched = 0
        if status and "UPDATE" in status:
            try:
                matched = int(status.split()[-1])
            except Exception:
                matched = 0
        return UpdateResult(matched, matched)

    async def delete_many(self, filter_query: Dict[str, Any]) -> DeleteResult:
        where, params = self._where(filter_query)
        status = await self._db.execute(f"DELETE FROM pages {where}", *params)
        deleted = 0
        if status and "DELETE" in status:
            try:
                deleted = int(status.split()[-1])
            except Exception:
                deleted = 0
        return DeleteResult(deleted)

    async def count_documents(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS count FROM pages {where}", *params)
        return int(row["count"]) if row else 0


class HistoryCollection:
    def __init__(self, db: Database):
        self._db = db

    def _where(
        self, filter_query: Optional[Dict[str, Any]], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(filter_query, allow_or=True, start_index=start_index)

    async def find_one(
        self, filter_query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM history {where} ORDER BY updated_at DESC LIMIT 1", *params
        )
        return _record_to_dict(row)

    def find(
        self, filter_query: Optional[Dict[str, Any]] = None, projection: Any = None
    ) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                order_parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(order_parts)
            limit_clause = ""
            if limit is not None:
                limit_clause = f" LIMIT {limit}"
            offset_clause = ""
            if skip:
                offset_clause = f" OFFSET {skip}"
            rows = await self._db.fetch(
                f"SELECT * FROM history {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            return [_record_to_dict(r) for r in rows if r is not None]

        return Cursor(_fetch)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO history ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)

    async def update_many(
        self, filter_query: Dict[str, Any], update: Dict[str, Any]
    ) -> UpdateResult:
        set_data = update.get("$set", {}) if "$set" in update else update
        if not set_data:
            return UpdateResult(0, 0)
        params: List[Any] = []
        assignments: List[str] = []
        for key, value in set_data.items():
            assignments.append(f"{key} = ${len(params)+1}")
            params.append(value)
        where, where_params = self._where(filter_query, start_index=len(params))
        params.extend(where_params)
        status = await self._db.execute(
            f"UPDATE history SET {', '.join(assignments)} {where}", *params
        )
        matched = 0
        if status and "UPDATE" in status:
            try:
                matched = int(status.split()[-1])
            except Exception:
                matched = 0
        return UpdateResult(matched, matched)

    async def count_documents(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS count FROM history {where}", *params)
        return int(row["count"]) if row else 0


class BranchesCollection:
    def __init__(self, db: Database):
        self._db = db

    def _where(
        self, filter_query: Optional[Dict[str, Any]], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(filter_query, allow_or=True, start_index=start_index)

    def find(self, filter_query: Optional[Dict[str, Any]] = None) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(parts)
            limit_clause = f" LIMIT {limit}" if limit is not None else ""
            offset_clause = f" OFFSET {skip}" if skip else ""
            rows = await self._db.fetch(
                f"SELECT * FROM branches {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            return [_record_to_dict(r) for r in rows if r is not None]

        return Cursor(_fetch)

    async def find_one(
        self, filter_query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM branches {where} LIMIT 1", *params
        )
        return _record_to_dict(row)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO branches ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)

    async def delete_one(self, filter_query: Dict[str, Any]) -> DeleteResult:
        where, params = self._where(filter_query)
        status = await self._db.execute(f"DELETE FROM branches {where}", *params)
        deleted = 0
        if status and "DELETE" in status:
            try:
                deleted = int(status.split()[-1])
            except Exception:
                deleted = 0
        return DeleteResult(deleted)

    async def update_many(
        self, filter_query: Dict[str, Any], update: Dict[str, Any]
    ) -> UpdateResult:
        set_data = update.get("$set", {}) if "$set" in update else update
        if not set_data:
            return UpdateResult(0, 0)
        params: List[Any] = []
        assignments: List[str] = []
        for key, value in set_data.items():
            assignments.append(f"{key} = ${len(params)+1}")
            params.append(value)
        where, where_params = self._where(filter_query, start_index=len(params))
        params.extend(where_params)
        status = await self._db.execute(
            f"UPDATE branches SET {', '.join(assignments)} {where}", *params
        )
        matched = 0
        if status and "UPDATE" in status:
            try:
                matched = int(status.split()[-1])
            except Exception:
                matched = 0
        return UpdateResult(matched, matched)

    async def count_documents(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS count FROM branches {where}", *params)
        return int(row["count"]) if row else 0


class UsersCollection:
    def __init__(self, db: Database):
        self._db = db
        self._json_fields = {"favorites", "page_edits"}

    def _encode_value(self, key: str, value: Any) -> Any:
        if key in self._json_fields:
            return json.dumps(value)
        return value

    def _decode_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        for key in self._json_fields:
            if key in document:
                value = document[key]
                if isinstance(value, str):
                    try:
                        document[key] = json.loads(value)
                    except Exception:
                        pass
        return document

    def _where(self, filter_query: Optional[Dict[str, Any]]) -> Tuple[str, List[Any]]:
        if not filter_query:
            return "", []

        params: List[Any] = []

        def handle(node: Dict[str, Any]) -> str:
            clauses: List[str] = []
            for raw_key, value in node.items():
                if isinstance(value, dict) and "$regex" in value:
                    pattern = value["$regex"]
                    options = value.get("$options", "").lower()
                    if pattern.startswith("^") and pattern.endswith("$"):
                        pattern = pattern[1:-1]
                    comparator = "ILIKE" if "i" in options else "LIKE"
                    params.append(pattern)
                    clauses.append(f"username {comparator} ${len(params)}")
                else:
                    params.append(value)
                    clauses.append(f"{raw_key} = ${len(params)}")
            return "(" + " AND ".join(clauses) + ")"

        clause = handle(filter_query)
        return f"WHERE {clause}", params

    async def find_one(
        self, filter_query: Dict[str, Any], projection: Optional[Dict[str, int]] = None
    ) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM users {where} LIMIT 1", *params
        )
        result = _record_to_dict(row)
        if result:
            result = self._decode_document(result)
        if result and projection:
            include_keys = {k for k, v in projection.items() if v}
            if include_keys:
                result = {key: value for key, value in result.items() if key in include_keys}
        return result

    def find(
        self, filter_query: Optional[Dict[str, Any]] = None, projection: Any = None
    ) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(parts)
            limit_clause = f" LIMIT {limit}" if limit is not None else ""
            offset_clause = f" OFFSET {skip}" if skip else ""
            rows = await self._db.fetch(
                f"SELECT * FROM users {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            results: List[Dict[str, Any]] = []
            for r in rows:
                if r is None:
                    continue
                decoded = self._decode_document(_record_to_dict(r))
                results.append(decoded)
            if projection:
                include_keys = {k for k, v in projection.items() if v}
                if include_keys:
                    trimmed = []
                    for item in results:
                        trimmed.append(
                            {key: val for key, val in item.items() if key in include_keys}
                        )
                    results = trimmed
            return results

        return Cursor(_fetch)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [self._encode_value(col, document[col]) for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO users ({col_list}) VALUES ({placeholders}) RETURNING username",
            *values,
        )
        inserted_id = row["username"] if row else None
        return InsertOneResult(inserted_id)

    async def update_one(
        self, filter_query: Dict[str, Any], update: Dict[str, Any]
    ) -> UpdateResult:
        existing = await self.find_one(filter_query)
        if not existing:
            return UpdateResult(0, 0)

        new_doc = dict(existing)
        modified = False

        if "$inc" in update:
            for key, inc in update["$inc"].items():
                if key.startswith("page_edits."):
                    page = key.split(".", 1)[1]
                    edits = dict(new_doc.get("page_edits") or {})
                    edits[page] = int(edits.get(page, 0) or 0) + inc
                    new_doc["page_edits"] = edits
                else:
                    new_doc[key] = int(new_doc.get(key, 0) or 0) + inc
                modified = True

        if "$set" in update:
            for key, value in update["$set"].items():
                if key.startswith("page_edits."):
                    page = key.split(".", 1)[1]
                    edits = dict(new_doc.get("page_edits") or {})
                    edits[page] = value
                    new_doc["page_edits"] = edits
                else:
                    new_doc[key] = value
                modified = True

        if "$unset" in update:
            for key in update["$unset"].keys():
                if key.startswith("page_edits."):
                    page = key.split(".", 1)[1]
                    edits = dict(new_doc.get("page_edits") or {})
                    edits.pop(page, None)
                    new_doc["page_edits"] = edits
                else:
                    new_doc.pop(key, None)
                modified = True

        if "$addToSet" in update:
            favorites = list(new_doc.get("favorites") or [])
            fav = update["$addToSet"].get("favorites")
            if fav is not None and fav not in favorites:
                favorites.append(fav)
                new_doc["favorites"] = favorites
                modified = True

        if "$pull" in update:
            favorites = list(new_doc.get("favorites") or [])
            removal = update["$pull"].get("favorites")
            new_doc["favorites"] = [
                item for item in favorites if item != removal
            ]
            modified = True

        if not modified:
            return UpdateResult(1, 0)

        params: List[Any] = []
        assignments: List[str] = []
        for key, value in new_doc.items():
            if key == "username":
                continue
            assignments.append(f"{key} = ${len(params)+1}")
            params.append(self._encode_value(key, value))
        params.append(existing["username"])
        await self._db.execute(
            f"UPDATE users SET {', '.join(assignments)} WHERE username = ${len(params)}",
            *params,
        )
        return UpdateResult(1, 1)


class SessionsCollection:
    def __init__(self, db: Database):
        self._db = db

    def _where(
        self, filter_query: Dict[str, Any], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(filter_query, start_index=start_index)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO sessions ({col_list}) VALUES ({placeholders}) RETURNING session_id",
            *values,
        )
        inserted_id = row["session_id"] if row else None
        return InsertOneResult(inserted_id)

    async def find_one(self, filter_query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM sessions {where} LIMIT 1", *params
        )
        return _record_to_dict(row)

    async def delete_one(self, filter_query: Dict[str, Any]) -> DeleteResult:
        where, params = self._where(filter_query)
        status = await self._db.execute(f"DELETE FROM sessions {where}", *params)
        deleted = 0
        if status and "DELETE" in status:
            try:
                deleted = int(status.split()[-1])
            except Exception:
                deleted = 0
        return DeleteResult(deleted)


class ImageHashesCollection:
    def __init__(self, db: Database):
        self._db = db

    def _where(
        self, filter_query: Optional[Dict[str, Any]], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(filter_query, start_index=start_index)

    def find(self, filter_query: Optional[Dict[str, Any]] = None) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(parts)
            limit_clause = f" LIMIT {limit}" if limit is not None else ""
            offset_clause = f" OFFSET {skip}" if skip else ""
            rows = await self._db.fetch(
                f"SELECT * FROM image_hashes {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            return [_record_to_dict(r) for r in rows if r is not None]

        return Cursor(_fetch)

    async def find_one(self, filter_query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(
            f"SELECT * FROM image_hashes {where} LIMIT 1", *params
        )
        return _record_to_dict(row)

    async def update_one(
        self, filter_query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False
    ) -> UpdateResult:
        existing = await self.find_one(filter_query)
        if not existing and not upsert:
            return UpdateResult(0, 0)
        set_data = update.get("$set", {}) if "$set" in update else update
        if existing:
            params: List[Any] = []
            assignments: List[str] = []
            for key, value in set_data.items():
                assignments.append(f"{key} = ${len(params)+1}")
                params.append(value)
            where, where_params = self._where(filter_query, start_index=len(params))
            params.extend(where_params)
            await self._db.execute(
                f"UPDATE image_hashes SET {', '.join(assignments)} {where}", *params
            )
            return UpdateResult(1, 1)
        else:
            doc = dict(filter_query)
            doc.update(set_data)
            await self.insert_one(doc)
            return UpdateResult(1, 1)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO image_hashes ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)

    async def count_documents(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS count FROM image_hashes {where}", *params)
        return int(row["count"]) if row else 0


class SettingsCollection:
    def __init__(self, db: Database):
        self._db = db

    async def find_one(self, filter_query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = filter_query.get("_id") or filter_query.get("key")
        row = await self._db.fetchrow(
            "SELECT key AS _id, data FROM settings WHERE key = $1 LIMIT 1", key
        )
        if row is None:
            return None
        data = dict(row["data"] or {})
        data["_id"] = row["key"]
        return data

    async def update_one(
        self, filter_query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False
    ) -> UpdateResult:
        key = filter_query.get("_id") or filter_query.get("key")
        set_data = update.get("$set", {}) if "$set" in update else update
        row = await self._db.fetchrow(
            "SELECT key FROM settings WHERE key = $1 LIMIT 1", key
        )
        if row:
            await self._db.execute(
                "UPDATE settings SET data = $1, updated_at = now() WHERE key = $2",
                set_data,
                key,
            )
            return UpdateResult(1, 1)
        if not row and not upsert:
            return UpdateResult(0, 0)
        await self._db.execute(
            "INSERT INTO settings (key, data) VALUES ($1, $2)", key, set_data
        )
        return UpdateResult(1, 1)


class SystemLogsCollection:
    def __init__(self, db: Database):
        self._db = db
        self._json_fields = {"metadata"}

    def _encode_value(self, key: str, value: Any) -> Any:
        if key in self._json_fields:
            return json.dumps(value)
        return value

    def _decode_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        for key in self._json_fields:
            if key in document:
                value = document[key]
                if isinstance(value, str):
                    try:
                        document[key] = json.loads(value)
                    except Exception:
                        pass
        return document

    def _where(
        self, filter_query: Optional[Dict[str, Any]], start_index: int = 0
    ) -> Tuple[str, List[Any]]:
        return _build_simple_where(filter_query, start_index=start_index)

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [self._encode_value(col, document[col]) for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO system_logs ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)

    def find(self, filter_query: Optional[Dict[str, Any]] = None) -> Cursor:
        async def _fetch(
            sort: List[Tuple[str, str]], limit: Optional[int], skip: int
        ) -> List[Dict[str, Any]]:
            where, params = self._where(filter_query)
            order_clause = ""
            if sort:
                parts = [f"{field} {direction}" for field, direction in sort]
                order_clause = " ORDER BY " + ", ".join(parts)
            limit_clause = f" LIMIT {limit}" if limit is not None else ""
            offset_clause = f" OFFSET {skip}" if skip else ""
            rows = await self._db.fetch(
                f"SELECT * FROM system_logs {where}{order_clause}{limit_clause}{offset_clause}",
                *params,
            )
            return [
                self._decode_document(_record_to_dict(r))
                for r in rows
                if r is not None
            ]

        return Cursor(_fetch)

    async def count_documents(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._where(filter_query)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS count FROM system_logs {where}", *params)
        return int(row["count"]) if row else 0


class AnalyticsEventsCollection:
    def __init__(self, db: Database):
        self._db = db

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        columns = list(document.keys())
        values = [document[col] for col in columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        col_list = ", ".join(columns)
        row = await self._db.fetchrow(
            f"INSERT INTO analytics_events ({col_list}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        inserted_id = row["id"] if row else None
        return InsertOneResult(inserted_id)


# Global database instance and helpers
db_instance = Database()


def get_pages_collection():
    return db_instance.get_collection("pages")


def get_history_collection():
    return db_instance.get_collection("history")


def get_branches_collection():
    return db_instance.get_collection("branches")


def get_users_collection():
    return db_instance.get_collection("users")


def get_image_hashes_collection():
    return db_instance.get_collection("image_hashes")


async def init_database() -> None:
    """Initialize the database connection and apply migrations."""
    try:
        await db_instance.connect()
    except Exception:
        logger.exception("Error initializing database")
