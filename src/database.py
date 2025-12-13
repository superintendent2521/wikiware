"""
Postgres-backed database layer for WikiWare.

This module replaces the previous MongoDB driver with a lightweight JSONB
storage model in Postgres while preserving the collection-style API used by
the rest of the codebase.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional

import asyncpg
from dotenv import load_dotenv
from loguru import logger

from . import config

load_dotenv()

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/wikiware"
)
DB_OPERATION_LOG_THRESHOLD_MS = (
    float(os.getenv("DB_OPERATION_LOG_THRESHOLD_MS", "100")) / 1000
)
TABLE_NAME = "wikiware_documents"


# ---------------------- Result helpers ----------------------


@dataclass
class InsertOneResult:
    inserted_id: Optional[str]


@dataclass
class UpdateResult:
    matched_count: int
    modified_count: int
    upserted_id: Optional[str] = None


@dataclass
class DeleteResult:
    deleted_count: int


# ---------------------- Utility helpers ----------------------


def _ensure_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _get_by_path(doc: Dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    current: Any = doc
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_by_path(doc: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _apply_projection(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    if projection is None:
        return doc
    include_keys = {k for k, v in projection.items() if v}
    exclude_keys = {k for k, v in projection.items() if not v}

    if include_keys:
        projected = {k: doc[k] for k in include_keys if k in doc}
        if "_id" in doc and "_id" not in projected and projection.get("_id", 1):
            projected["_id"] = doc["_id"]
        return projected

    if exclude_keys:
        return {k: v for k, v in doc.items() if k not in exclude_keys}

    return doc


def _matches_filter(doc: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    if not filt:
        return True

    def compare(val: Any, condition: Any) -> bool:
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$gte" and not (val is not None and val >= expected):
                    return False
                if op == "$gt" and not (val is not None and val > expected):
                    return False
                if op == "$lte" and not (val is not None and val <= expected):
                    return False
                if op == "$lt" and not (val is not None and val < expected):
                    return False
                if op == "$in" and val not in expected:
                    return False
                if op == "$nin" and val in expected:
                    return False
            return True
        return val == condition

    for key, expected in filt.items():
        value = _get_by_path(doc, key) if "." in key else doc.get(key)
        if not compare(value, expected):
            return False
    return True


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(doc)
    for op, changes in update.items():
        if op == "$set":
            for path, value in changes.items():
                _set_by_path(updated, path, value)
        elif op == "$inc":
            for path, delta in changes.items():
                current = _get_by_path(updated, path)
                if current is None:
                    _set_by_path(updated, path, delta)
                else:
                    _set_by_path(updated, path, current + delta)
        else:
            logger.warning("Unsupported update operator {}", op)
    return updated


def _project_sort_key(doc: Dict[str, Any], key: str) -> Any:
    if "." in key:
        return _get_by_path(doc, key)
    return doc.get(key)


# ---------------------- Cursor ----------------------


class PostgresCursor:
    def __init__(
        self,
        loader: Callable[[], asyncio.Future],
        *,
        limit: Optional[int] = None,
    ):
        self._loader = loader
        self._docs: Optional[List[Dict[str, Any]]] = None
        self._sorts: List[tuple[str, int]] = []
        self._limit = limit

    async def _ensure_loaded(self) -> None:
        if self._docs is None:
            self._docs = await self._loader()
            self._apply_sorts_and_limit()

    def _apply_sorts_and_limit(self) -> None:
        if self._docs is None:
            return
        for key, direction in reversed(self._sorts):
            self._docs.sort(key=lambda d: _project_sort_key(d, key) or 0, reverse=direction < 0)
        if self._limit is not None:
            self._docs = self._docs[: self._limit]

    def sort(self, key: str, direction: int = 1) -> "PostgresCursor":
        self._sorts.append((key, direction))
        return self

    async def to_list(self, length: Optional[int]) -> List[Dict[str, Any]]:
        await self._ensure_loaded()
        if self._docs is None:
            return []
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])

    def __aiter__(self) -> AsyncIterator[Dict[str, Any]]:
        async def iterator():
            await self._ensure_loaded()
            for doc in self._docs or []:
                yield doc

        return iterator()

    def __iter__(self) -> Iterable[Dict[str, Any]]:
        loop = _ensure_loop()
        if self._docs is None:
            if loop and loop.is_running():
                raise RuntimeError("Synchronous iteration is not supported while loop is running.")
            asyncio.run(self._ensure_loaded())
        return iter(self._docs or [])


# ---------------------- Collection ----------------------


class PostgresCollection:
    def __init__(self, name: str, db: "Database"):
        self.name = name
        self._db = db

    async def _fetch_docs(self) -> List[Dict[str, Any]]:
        rows = await self._db.fetch(
            f"SELECT doc FROM {TABLE_NAME} WHERE collection = $1",
            self.name,
        )
        return [row["doc"] for row in rows]

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        limit: Optional[int] = None,
    ) -> PostgresCursor:
        async def loader():
            docs = await self._fetch_docs()
            matched = [doc for doc in docs if _matches_filter(doc, filt or {})]
            if projection:
                matched = [_apply_projection(doc, projection) for doc in matched]
            return matched

        return PostgresCursor(loader, limit=limit)

    async def find_one(self, filt: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        cursor = self.find(filt, limit=1)
        results = await cursor.to_list(1)
        return results[0] if results else None

    async def insert_one(self, document: Dict[str, Any]) -> InsertOneResult:
        doc = dict(document)
        if "_id" not in doc:
            doc["_id"] = str(uuid.uuid4())
        await self._db.execute(
            f"""
            INSERT INTO {TABLE_NAME} (collection, id, doc)
            VALUES ($1, $2, $3)
            ON CONFLICT (collection, id) DO UPDATE SET doc = EXCLUDED.doc
            """,
            self.name,
            str(doc["_id"]),
            doc,
        )
        return InsertOneResult(inserted_id=str(doc["_id"]))

    async def update_one(
        self,
        filt: Dict[str, Any],
        update: Dict[str, Any],
        *,
        upsert: bool = False,
    ) -> UpdateResult:
        existing = await self.find_one(filt)
        if existing is None:
            if not upsert:
                return UpdateResult(matched_count=0, modified_count=0, upserted_id=None)
            base = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            new_doc = _apply_update(base, update)
            result = await self.insert_one(new_doc)
            return UpdateResult(matched_count=0, modified_count=1, upserted_id=result.inserted_id)

        updated_doc = _apply_update(existing, update)
        await self._db.execute(
            f"UPDATE {TABLE_NAME} SET doc = $3 WHERE collection = $1 AND id = $2",
            self.name,
            str(existing.get("_id")),
            updated_doc,
        )
        return UpdateResult(matched_count=1, modified_count=1, upserted_id=None)

    async def delete_one(self, filt: Dict[str, Any]) -> DeleteResult:
        existing = await self.find_one(filt)
        if existing is None:
            return DeleteResult(deleted_count=0)
        await self._db.execute(
            f"DELETE FROM {TABLE_NAME} WHERE collection = $1 AND id = $2",
            self.name,
            str(existing.get("_id")),
        )
        return DeleteResult(deleted_count=1)

    async def count_documents(self, filt: Optional[Dict[str, Any]] = None) -> int:
        docs = await self._fetch_docs()
        return sum(1 for doc in docs if _matches_filter(doc, filt or {}))

    async def distinct(self, key: str, filt: Optional[Dict[str, Any]] = None) -> List[Any]:
        docs = await self._fetch_docs()
        values = []
        for doc in docs:
            if filt and not _matches_filter(doc, filt):
                continue
            value = _get_by_path(doc, key) if "." in key else doc.get(key)
            if value is not None:
                values.append(value)
        return list({v for v in values})

    def aggregate(self, pipeline: List[Dict[str, Any]]) -> PostgresCursor:
        async def loader():
            docs = await self._fetch_docs()
            for stage in pipeline:
                if "$match" in stage:
                    docs = [doc for doc in docs if _matches_filter(doc, stage["$match"])]
                elif "$project" in stage:
                    projected = []
                    for doc in docs:
                        new_doc: Dict[str, Any] = {}
                        for key, expr in stage["$project"].items():
                            if expr == 1:
                                new_doc[key] = doc.get(key)
                            elif isinstance(expr, str) and expr.startswith("$"):
                                    new_doc[key] = _get_by_path(doc, expr[1:]) if "." in expr[1:] else doc.get(expr[1:])
                            elif isinstance(expr, dict) and "$dateToString" in expr:
                                fmt = expr["$dateToString"]["format"]
                                date_val = _get_by_path(doc, expr["$dateToString"]["date"][1:])
                                new_doc[key] = date_val.strftime(fmt) if hasattr(date_val, "strftime") else None
                            else:
                                new_doc[key] = expr
                        projected.append(new_doc)
                    docs = projected or docs
                elif "$group" in stage:
                    grouped: Dict[Any, Dict[str, Any]] = {}
                    group_spec = stage["$group"]
                    for doc in docs:
                        group_id_expr = group_spec.get("_id")
                        if isinstance(group_id_expr, dict):
                            group_id = {}
                            for k, v in group_id_expr.items():
                                if isinstance(v, dict) and "$dateToString" in v:
                                    date_val = _get_by_path(doc, v["$dateToString"]["date"][1:])
                                    fmt = v["$dateToString"]["format"]
                                    group_id[k] = date_val.strftime(fmt) if hasattr(date_val, "strftime") else None
                                elif isinstance(v, str) and v.startswith("$"):
                                    group_id[k] = _get_by_path(doc, v[1:]) if "." in v[1:] else doc.get(v[1:])
                                else:
                                    group_id[k] = v
                        elif isinstance(group_id_expr, str) and group_id_expr.startswith("$"):
                            group_id = _get_by_path(doc, group_id_expr[1:]) if "." in group_id_expr[1:] else doc.get(group_id_expr[1:])
                        else:
                            group_id = group_id_expr

                        if group_id not in grouped:
                            grouped[group_id] = {"_id": group_id}

                        for field, expr in group_spec.items():
                            if field == "_id":
                                continue
                            if isinstance(expr, dict) and "$sum" in expr:
                                val = expr["$sum"]
                                addend = 0
                                if isinstance(val, (int, float)):
                                    addend = val
                                elif isinstance(val, str) and val.startswith("$"):
                                    addend = _get_by_path(doc, val[1:]) if "." in val[1:] else doc.get(val[1:]) or 0
                                grouped[group_id][field] = grouped[group_id].get(field, 0) + (addend or 0)
                            elif isinstance(expr, dict) and "$max" in expr:
                                candidate = expr["$max"]
                                if isinstance(candidate, str) and candidate.startswith("$"):
                                    candidate = _get_by_path(doc, candidate[1:]) if "." in candidate[1:] else doc.get(candidate[1:])
                                current = grouped[group_id].get(field)
                                if current is None or (candidate is not None and candidate > current):
                                    grouped[group_id][field] = candidate
                            elif isinstance(expr, dict) and "$first" in expr:
                                if field not in grouped[group_id]:
                                    val = expr["$first"]
                                    if isinstance(val, str) and val.startswith("$"):
                                        val = _get_by_path(doc, val[1:]) if "." in val[1:] else doc.get(val[1:])
                                    grouped[group_id][field] = val
                    docs = list(grouped.values())
                elif "$sort" in stage:
                    for key, direction in reversed(list(stage["$sort"].items())):
                        docs.sort(key=lambda d: _project_sort_key(d, key) or 0, reverse=direction < 0)
                elif "$limit" in stage:
                    docs = docs[: stage["$limit"]]
            return docs

        return PostgresCursor(loader)


# ---------------------- Database ----------------------


class Database:
    """Manages Postgres connection and collection-style access."""

    def __init__(self, dsn: str = POSTGRES_DSN):
        self._dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None
        self.is_connected = False
        self._connection_lock = asyncio.Lock()
        self._wrapped_collections: Dict[str, PostgresCollection] = {}

    def _reset_state(self) -> None:
        self.pool = None
        self.is_connected = False
        self._wrapped_collections.clear()

    async def connect(self) -> None:
        async with self._connection_lock:
            if self.is_connected:
                return
            try:
                logger.info("Connecting to Postgres at {}", self._dsn)
                self.pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
                await self._ensure_table()
                self.is_connected = True
                logger.info("Connected to Postgres and storage table ensured")
            except Exception:
                logger.exception("Failed to connect to Postgres")
                self._reset_state()

    async def _ensure_table(self) -> None:
        if self.pool is None:
            return
        await self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                collection TEXT NOT NULL,
                id TEXT NOT NULL,
                doc JSONB NOT NULL,
                PRIMARY KEY (collection, id)
            );
            """
        )

    async def disconnect(self) -> None:
        async with self._connection_lock:
            if self.pool:
                await self.pool.close()
            self._reset_state()

    def get_collection(self, name: str) -> Optional[PostgresCollection]:
        if not self.is_connected:
            return None
        if name in self._wrapped_collections:
            return self._wrapped_collections[name]
        collection = PostgresCollection(name, self)
        self._wrapped_collections[name] = collection
        return collection

    async def execute(self, query: str, *args: Any) -> None:
        if self.pool is None:
            raise RuntimeError("Database not connected")
        async with self.pool.acquire() as conn:
            await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> List[asyncpg.Record]:
        if self.pool is None:
            raise RuntimeError("Database not connected")
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def get_pool_stats(self) -> Dict[str, Any]:
        if self.pool is None:
            return {"status": "not_connected"}
        return {
            "status": "connected",
            "pool_size": self.pool.max_size,
            "free": self.pool.free_size,
        }


# Global database instance
db_instance = Database()


# Collections
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


async def create_indexes() -> None:
    # JSONB storage uses application-level filtering; explicit DB indexes can be added later.
    logger.info("Index creation skipped (JSONB storage)")


async def init_database() -> None:
    try:
        await db_instance.connect()
        if db_instance.is_connected:
            await create_indexes()
            pool_stats = await db_instance.get_pool_stats()
            logger.info("Database pool stats: {}", pool_stats)
    except Exception:
        logger.error("Error initializing database")
