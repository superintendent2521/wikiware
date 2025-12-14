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
import json
import datetime as dt
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, List, Optional

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
TABLE_PREFIX = "wikiware_"
DEFAULT_COLLECTIONS = (
    "pages",
    "history",
    "branches",
    "users",
    "image_hashes",
    "analytics_events",
    "sessions",
    "settings",
    "system_logs",
)
INDEX_SPECS = {
    "pages": [
        ("title_branch", "((doc->>'title'), (doc->>'branch'))"),
        ("branch", "((doc->>'branch'))"),
        ("updated_at", "((doc->>'updated_at'))"),
        ("title_trgm", "USING gin ((doc->>'title') gin_trgm_ops)"),
    ],
    "history": [
        ("title_branch", "((doc->>'title'), (doc->>'branch'))"),
        ("updated_at", "((doc->>'updated_at'))"),
    ],
    "branches": [
        ("page_branch", "((doc->>'page_title'), (doc->>'branch_name'))"),
        ("created_at", "((doc->>'created_at'))"),
        ("branch_name", "((doc->>'branch_name'))"),
    ],
    "users": [
        ("username", "((doc->>'username'))"),
        ("email", "((doc->>'email'))"),
    ],
    "image_hashes": [
        ("filename", "((doc->>'filename'))"),
        ("sha256", "((doc->>'sha256'))"),
    ],
    "analytics_events": [
        ("event_type_timestamp", "((doc->>'event_type'), ((doc->>'timestamp')::timestamptz))"),
        ("query_normalized_trgm", "USING gin ((doc->>'query_normalized') gin_trgm_ops)"),
        ("timestamp_only", "(((doc->>'timestamp'))::timestamptz)"),
    ],
    "sessions": [
        ("session_id", "((doc->>'session_id'))"),
        ("expires_at", "(((doc->>'expires_at'))::timestamptz)"),
        ("user_id", "((doc->>'user_id'))"),
    ],
    "settings": [
        ("doc_id", "((doc->>'_id'))"),
    ],
    "system_logs": [
        ("action_timestamp", "((doc->>'action'), ((doc->>'timestamp')::timestamptz))"),
        ("timestamp_only", "(((doc->>'timestamp'))::timestamptz)"),
    ],
}


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

def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


# ---------------------- Cursor ----------------------


class PostgresCursor:
    def __init__(
        self,
        collection: "PostgresCollection",
        filt: Optional[Dict[str, Any]],
        projection: Optional[Dict[str, int]],
        limit: Optional[int] = None,
        *,
        pipeline: Optional[List[Dict[str, Any]]] = None,
    ):
        self._collection = collection
        self._filt = filt or {}
        self._projection = projection
        self._docs: Optional[List[Dict[str, Any]]] = None
        self._sorts: List[tuple[str, int]] = []
        self._limit = limit
        self._pipeline = pipeline

    async def _ensure_loaded(self) -> None:
        if self._docs is None:
            if self._pipeline is not None:
                self._docs = await self._collection._run_aggregate_pipeline(
                    self._pipeline, self._sorts, self._limit
                )
            else:
                self._docs = await self._collection._find_docs(
                    self._filt, self._projection, self._sorts, self._limit
                )

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
        raise RuntimeError(
            "Synchronous iteration is not supported for PostgresCursor. Use async iteration or to_list()."
        )


# ---------------------- Collection ----------------------


class PostgresCollection:
    def __init__(self, name: str, table_name: str, db: "Database"):
        self.name = name
        self._table_name = table_name
        self._db = db

    def _normalize_filter_value(self, value: Any) -> Any:
        if isinstance(value, dt.datetime):
            return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        if isinstance(value, (list, tuple)):
            return [self._normalize_filter_value(v) for v in value]
        return value

    def _value_type(self, value: Any) -> str:
        if isinstance(value, (int, float, Decimal)):
            return "numeric"
        if isinstance(value, dt.datetime):
            return "timestamptz"
        if isinstance(value, dt.date):
            return "date"
        if isinstance(value, bool):
            return "boolean"
        return "text"

    def _value_type_for_iterable(self, values: Iterable[Any]) -> str:
        for val in values:
            return self._value_type(val)
        return "text"

    def _cast_expr_for_type(self, expr: str, value_type: str) -> str:
        casts = {
            "numeric": "::numeric",
            "timestamptz": "::timestamptz",
            "date": "::date",
            "boolean": "::boolean",
        }
        suffix = casts.get(value_type)
        return f"({expr}){suffix}" if suffix else expr

    def _append_param(self, value: Any, value_type: str, params: List[Any], *, is_array: bool = False) -> str:
        normalized = self._normalize_filter_value(value)
        params.append(normalized)
        placeholder = f"${len(params)}"
        if is_array:
            casts = {
                "numeric": "::numeric[]",
                "timestamptz": "::timestamptz[]",
                "date": "::date[]",
                "boolean": "::boolean[]",
            }
            return f"{placeholder}{casts.get(value_type, '::text[]')}"
        casts = {
            "numeric": "::numeric",
            "timestamptz": "::timestamptz",
            "date": "::date",
            "boolean": "::boolean",
        }
        return f"{placeholder}{casts.get(value_type, '::text')}"

    def _add_path_param(self, path: str, params: List[Any]) -> str:
        parts = [p for p in path.split(".") if p]
        params.append(parts)
        return f"${len(params)}::text[]"

    async def _ensure_table(self) -> None:
        await self._db.ensure_table(self._table_name)

    def _json_path_expr(self, path: str, *, as_text: bool = True) -> str:
        if not path:
            raise ValueError(f"Invalid field path '{path}'")
        if path == "_id":
            return "id"
        safe_parts = [json.dumps(part)[1:-1] for part in path.split(".")]
        path_literal = '","'.join(safe_parts)
        operator = "#>>" if as_text else "#>"
        return f'doc {operator} \'{{"{path_literal}"}}\''

    def _build_where_clause(self, filt: Optional[Dict[str, Any]], params: List[Any]) -> str:
        if not filt:
            return ""

        def _param_value(value: Any) -> str:
            normalized = _jsonable(value)
            if isinstance(normalized, (dict, list)):
                return json.dumps(normalized, ensure_ascii=False)
            return normalized

        clauses: List[str] = []
        for key, condition in filt.items():
            if key in ("$and", "$or"):
                if not isinstance(condition, list):
                    continue
                nested_clauses = []
                for sub_filter in condition:
                    nested = self._build_where_clause(sub_filter, params)
                    if nested:
                        nested_clauses.append(f"({nested})")
                if nested_clauses:
                    joiner = " AND " if key == "$and" else " OR "
                    clauses.append(joiner.join(nested_clauses))
                continue
            expr = self._json_path_expr(key)
            if isinstance(condition, dict):
                if "$regex" in condition:
                    pattern = condition.get("$regex") or ""
                    options = condition.get("$options", "")
                    params.append(pattern)
                    operator = "~*" if "i" in options else "~"
                    clauses.append(f"{expr} {operator} ${len(params)}")
                    continue
                if "$like" in condition or "$ilike" in condition:
                    op_key = "$ilike" if "$ilike" in condition else "$like"
                    operator = "ILIKE" if op_key == "$ilike" else "LIKE"
                    params.append(_param_value(condition[op_key]))
                    clauses.append(f"{expr} {operator} ${len(params)}")
                    continue
                for op, expected in condition.items():
                    if op == "$options":
                        continue
                    raw_expected = expected
                    if raw_expected is None:
                        clauses.append(f"{expr} IS NULL")
                        continue
                    if op == "$exists":
                        clauses.append(f"{expr} IS NOT NULL" if raw_expected else f"{expr} IS NULL")
                        continue
                    value_type = self._value_type(raw_expected)
                    typed_expr = self._cast_expr_for_type(expr, value_type)
                    if op == "$gte":
                        placeholder = self._append_param(raw_expected, value_type, params)
                        clauses.append(f"{typed_expr} >= {placeholder}")
                    elif op == "$gt":
                        placeholder = self._append_param(raw_expected, value_type, params)
                        clauses.append(f"{typed_expr} > {placeholder}")
                    elif op == "$lte":
                        placeholder = self._append_param(raw_expected, value_type, params)
                        clauses.append(f"{typed_expr} <= {placeholder}")
                    elif op == "$lt":
                        placeholder = self._append_param(raw_expected, value_type, params)
                        clauses.append(f"{typed_expr} < {placeholder}")
                    elif op == "$in":
                        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
                        value_type = self._value_type_for_iterable(values)
                        placeholder = self._append_param(list(values), value_type, params, is_array=True)
                        typed_expr = self._cast_expr_for_type(expr, value_type)
                        clauses.append(f"{typed_expr} = ANY({placeholder})")
                    elif op == "$nin":
                        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
                        value_type = self._value_type_for_iterable(values)
                        placeholder = self._append_param(list(values), value_type, params, is_array=True)
                        typed_expr = self._cast_expr_for_type(expr, value_type)
                        clauses.append(f"{typed_expr} <> ALL({placeholder})")
                    else:
                        placeholder = self._append_param(raw_expected, value_type, params)
                        clauses.append(f"{typed_expr} = {placeholder}")
            else:
                if condition is None:
                    clauses.append(f"{expr} IS NULL")
                else:
                    value_type = self._value_type(condition)
                    typed_expr = self._cast_expr_for_type(expr, value_type)
                    placeholder = self._append_param(condition, value_type, params)
                    clauses.append(f"{typed_expr} = {placeholder}")

        if not clauses:
            return ""
        return " AND ".join(clauses)

    def _build_projection_clause(
        self, projection: Optional[Dict[str, int]]
    ) -> tuple[str, Optional[Dict[str, int]]]:
        if projection is None:
            return "doc AS doc", None

        include_keys = [k for k, v in projection.items() if v]
        if include_keys:
            parts = []
            for key in include_keys:
                parts.append(f"'{key}', {self._json_path_expr(key, as_text=False)}")
            if "_id" not in include_keys and projection.get("_id", 1):
                parts.append("'_id', id")
            fields = ", ".join(parts)
            return f"jsonb_strip_nulls(jsonb_build_object({fields})) AS doc", None

        # Defer exclusion projection to Python, as includes were explicitly not requested
        return "doc AS doc", projection

    def _build_order_clause(self, sorts: List[tuple[str, int]]) -> str:
        if not sorts:
            return ""
        parts = []
        for key, direction in sorts:
            expr = self._json_path_expr(key)
            order = "DESC" if direction < 0 else "ASC"
            parts.append(f"{expr} {order}")
        return ", ".join(parts)

    def _decode_row(self, row: asyncpg.Record, projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
        doc = row["doc"]
        if projection:
            doc = _apply_projection(doc, projection)
        return doc

    def _build_update_expression(self, update: Dict[str, Any], params: List[Any]) -> str:
        if not update:
            raise ValueError("Empty update payload is not supported")

        expr = "doc"

        for path, value in update.get("$set", {}).items():
            path_placeholder = self._add_path_param(path, params)
            params.append(_jsonable(self._normalize_filter_value(value)))
            value_placeholder = f"${len(params)}::jsonb"
            expr = f"jsonb_set({expr}, {path_placeholder}, {value_placeholder}, true)"

        for path, delta in update.get("$inc", {}).items():
            path_placeholder = self._add_path_param(path, params)
            params.append(_jsonable(self._normalize_filter_value(delta)))
            delta_placeholder = f"${len(params)}::numeric"
            current_numeric = f"COALESCE(({expr} #>> {path_placeholder})::numeric, 0)"
            increment_expr = f"to_jsonb({current_numeric} + {delta_placeholder})"
            expr = f"jsonb_set({expr}, {path_placeholder}, {increment_expr}, true)"

        return expr

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        limit: Optional[int] = None,
    ) -> PostgresCursor:
        return PostgresCursor(self, filt, projection, limit)

    async def _find_docs(
        self,
        filt: Optional[Dict[str, Any]],
        projection: Optional[Dict[str, int]],
        sorts: List[tuple[str, int]],
        limit: Optional[int],
        *,
        connection: Optional[asyncpg.Connection] = None,
    ) -> List[Dict[str, Any]]:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        projection_clause, projection_for_python = self._build_projection_clause(projection)
        order_clause = self._build_order_clause(sorts)

        query = f"SELECT {projection_clause} FROM {self._table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        if order_clause:
            query += f" ORDER BY {order_clause}"
        if limit is not None:
            params.append(limit)
            query += f" LIMIT ${len(params)}"

        rows = await self._db.fetch(query, *params, conn=connection)
        return [self._decode_row(row, projection_for_python) for row in rows]

    async def find_one(self, filt: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        results = await self._find_docs(filt, None, [], 1)
        return results[0] if results else None

    async def insert_one(
        self, document: Dict[str, Any], *, connection: Optional[asyncpg.Connection] = None
    ) -> InsertOneResult:
        await self._ensure_table()
        doc = dict(document)
        if "_id" not in doc:
            doc["_id"] = str(uuid.uuid4())
        json_payload = json.dumps(_jsonable(doc), ensure_ascii=False)
        await self._db.execute(
            f"""
            INSERT INTO {self._table_name} (id, doc)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc
            """,
            str(doc["_id"]),
            json_payload,
            conn=connection,
        )
        return InsertOneResult(inserted_id=str(doc["_id"]))

    async def update_one(
        self,
        filt: Dict[str, Any],
        update: Dict[str, Any],
        *,
        upsert: bool = False,
        connection: Optional[asyncpg.Connection] = None,
    ) -> UpdateResult:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        update_expr = self._build_update_expression(update, params)

        target_cte = f"SELECT id FROM {self._table_name}"
        if where_clause:
            target_cte += f" WHERE {where_clause}"
        target_cte += " LIMIT 1"

        query = (
            f"WITH target AS ({target_cte}) "
            f"UPDATE {self._table_name} SET doc = {update_expr} "
            f"FROM target WHERE target.id = {self._table_name}.id "
            "RETURNING 1"
        )

        rows = await self._db.fetch(query, *params, conn=connection)
        if rows:
            return UpdateResult(matched_count=len(rows), modified_count=len(rows), upserted_id=None)

        if upsert:
            base = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            new_doc = _apply_update(base, update)
            result = await self.insert_one(new_doc, connection=connection)
            return UpdateResult(matched_count=0, modified_count=1, upserted_id=result.inserted_id)

        return UpdateResult(matched_count=0, modified_count=0, upserted_id=None)
    async def delete_one(
        self, filt: Dict[str, Any], *, connection: Optional[asyncpg.Connection] = None
    ) -> DeleteResult:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)

        target_cte = f"SELECT id FROM {self._table_name}"
        if where_clause:
            target_cte += f" WHERE {where_clause}"
        target_cte += " LIMIT 1"

        query = (
            f"WITH target AS ({target_cte}), "
            f"deleted AS ("
            f"DELETE FROM {self._table_name} t USING target "
            f"WHERE t.id = target.id RETURNING 1"
            f") "
            "SELECT COUNT(*) AS count FROM deleted"
        )

        rows = await self._db.fetch(query, *params, conn=connection)
        if not rows:
            return DeleteResult(deleted_count=0)
        return DeleteResult(deleted_count=int(rows[0]["count"]))

    async def count_documents(self, filt: Optional[Dict[str, Any]] = None) -> int:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        query = f"SELECT COUNT(*) AS count FROM {self._table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        rows = await self._db.fetch(query, *params)
        if not rows:
            return 0
        return int(rows[0]["count"])

    async def distinct(self, key: str, filt: Optional[Dict[str, Any]] = None) -> List[Any]:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        expr = self._json_path_expr(key)
        query = f"SELECT DISTINCT {expr} AS value FROM {self._table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        rows = await self._db.fetch(query, *params)
        return [row["value"] for row in rows if row["value"] is not None]

    async def delete_many(
        self, filt: Optional[Dict[str, Any]], *, connection: Optional[asyncpg.Connection] = None
    ) -> DeleteResult:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        query = f"DELETE FROM {self._table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += " RETURNING 1"
        rows = await self._db.fetch(query, *params, conn=connection)
        return DeleteResult(deleted_count=len(rows))

    async def update_many(
        self,
        filt: Dict[str, Any],
        update: Dict[str, Any],
        *,
        upsert: bool = False,
        connection: Optional[asyncpg.Connection] = None,
    ) -> UpdateResult:
        await self._ensure_table()
        params: List[Any] = []
        where_clause = self._build_where_clause(filt or {}, params)
        update_expr = self._build_update_expression(update, params)

        query = f"UPDATE {self._table_name} SET doc = {update_expr}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += " RETURNING 1"

        rows = await self._db.fetch(query, *params, conn=connection)
        matched = len(rows)
        modified = matched
        upserted_id = None

        if upsert and matched == 0:
            base = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            new_doc = _apply_update(base, update)
            result = await self.insert_one(new_doc, connection=connection)
            upserted_id = result.inserted_id
            modified += 1

        return UpdateResult(matched_count=matched, modified_count=modified, upserted_id=upserted_id)

    def aggregate(self, pipeline: List[Dict[str, Any]]) -> PostgresCursor:
        return PostgresCursor(self, {}, None, None, pipeline=pipeline)

    def _translate_date_format(self, fmt: str) -> str:
        replacements = {
            "%Y": "YYYY",
            "%m": "MM",
            "%d": "DD",
            "%H": "HH24",
            "%M": "MI",
            "%S": "SS",
        }
        for mongo_fmt, pg_fmt in replacements.items():
            fmt = fmt.replace(mongo_fmt, pg_fmt)
        return fmt

    def _value_expression(self, value: Any, params: List[Any]) -> str:
        if isinstance(value, dict) and "$dateToString" in value:
            spec = value["$dateToString"]
            fmt = self._translate_date_format(spec.get("format", ""))
            date_val = spec.get("date")
            if isinstance(date_val, str) and date_val.startswith("$"):
                date_expr = self._json_path_expr(date_val[1:])
                return f"to_char({date_expr}::timestamptz, '{fmt}')"
        if isinstance(value, str) and value.startswith("$"):
            return self._json_path_expr(value[1:])
        params.append(_jsonable(value))
        return f"${len(params)}"

    def _build_group_id_expr(self, group_spec: Any, params: List[Any]) -> tuple[str, List[str]]:
        if isinstance(group_spec, dict):
            parts = []
            group_by_exprs: List[str] = []
            for key, val in group_spec.items():
                expr = self._value_expression(val, params)
                parts.append(f"'{key}', {expr}")
                group_by_exprs.append(expr)
            return f"jsonb_build_object({', '.join(parts)})", group_by_exprs
        if group_spec is None:
            return "NULL", []
        expr = self._value_expression(group_spec, params)
        return expr, [expr]

    def _build_agg_expression(self, expr: Any, params: List[Any]) -> str:
        if isinstance(expr, dict):
            if "$sum" in expr:
                value_expr = self._value_expression(expr["$sum"], params)
                return f"SUM(COALESCE({value_expr}::numeric, 0))"
            if "$max" in expr:
                value_expr = self._value_expression(expr["$max"], params)
                return f"MAX({value_expr})"
            if "$first" in expr:
                value_expr = self._value_expression(expr["$first"], params)
                return f"COALESCE((ARRAY_AGG({value_expr}))[1], NULL)"
        return self._value_expression(expr, params)

    def _build_aggregate_order_clause(self, sorts: List[tuple[str, int]]) -> str:
        if not sorts:
            return ""
        parts = []
        for key, direction in sorts:
            if key.startswith("_id."):
                subkey = key.split(".", 1)[1]
                expr = f"_id ->> '{subkey}'"
            elif key == "_id":
                expr = "_id"
            else:
                expr = key
            order = "DESC" if direction < 0 else "ASC"
            parts.append(f"{expr} {order}")
        return ", ".join(parts)

    def _apply_project_stage(self, docs: List[Dict[str, Any]], project: Dict[str, Any]) -> List[Dict[str, Any]]:
        projected: List[Dict[str, Any]] = []
        for doc in docs:
            new_doc: Dict[str, Any] = {}
            for key, expr in project.items():
                if expr == 0:
                    continue
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
        return projected

    async def _run_aggregate_pipeline(
        self,
        pipeline: List[Dict[str, Any]],
        sorts: List[tuple[str, int]],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        await self._ensure_table()
        match_filter: Dict[str, Any] = {}
        group_stage: Optional[Dict[str, Any]] = None
        project_stage: Optional[Dict[str, Any]] = None
        sort_stage: Optional[Dict[str, int]] = None
        limit_stage: Optional[int] = None

        for stage in pipeline:
            if "$match" in stage:
                match_filter.update(stage["$match"])
            elif "$group" in stage:
                group_stage = stage["$group"]
            elif "$project" in stage:
                project_stage = stage["$project"]
            elif "$sort" in stage:
                sort_stage = stage["$sort"]
            elif "$limit" in stage:
                limit_stage = stage["$limit"]

        params: List[Any] = []
        where_clause = self._build_where_clause(match_filter, params)

        if group_stage is None:
            # Fall back to a find-like query if no grouping is requested
            combined_sorts: List[tuple[str, int]] = []
            if sort_stage:
                combined_sorts.extend(list(sort_stage.items()))
            combined_sorts.extend(sorts)
            effective_limit = limit_stage if limit_stage is not None else limit
            results = await self._find_docs(match_filter, None, combined_sorts, effective_limit)
            if project_stage:
                results = self._apply_project_stage(results, project_stage)
            return results

        group_id_expr, group_by_exprs = self._build_group_id_expr(group_stage.get("_id"), params)
        select_parts = [f"{group_id_expr} AS _id"]
        for field, expr in group_stage.items():
            if field == "_id":
                continue
            agg_expr = self._build_agg_expression(expr, params)
            select_parts.append(f"{agg_expr} AS {field}")

        query = f"SELECT {', '.join(select_parts)} FROM {self._table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        if group_by_exprs:
            query += " GROUP BY " + ", ".join(group_by_exprs)

        combined_sorts: List[tuple[str, int]] = []
        if sort_stage:
            combined_sorts.extend(list(sort_stage.items()))
        combined_sorts.extend(sorts)
        order_clause = self._build_aggregate_order_clause(combined_sorts)
        if order_clause:
            query += f" ORDER BY {order_clause}"

        effective_limit = limit_stage if limit_stage is not None else limit
        if effective_limit is not None:
            params.append(effective_limit)
            query += f" LIMIT ${len(params)}"

        rows = await self._db.fetch(query, *params)
        docs: List[Dict[str, Any]] = []
        for row in rows:
            docs.append({k: row[k] for k in row.keys()})

        if project_stage:
            docs = self._apply_project_stage(docs, project_stage)
        return docs


# ---------------------- Database ----------------------


class Database:
    """Manages Postgres connection and collection-style access."""

    def __init__(self, dsn: str = POSTGRES_DSN):
        self._dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None
        self.is_connected = False
        self._connection_lock = asyncio.Lock()
        self._wrapped_collections: Dict[str, PostgresCollection] = {}
        self._ensured_tables: set[str] = set()
        self._ensured_indexes: set[str] = set()
        self._ensured_extensions: set[str] = set()
        self._monitor_task: Optional[asyncio.Task] = None

    def _reset_state(self, *, preserve_monitor: bool = False) -> None:
        self.pool = None
        self.is_connected = False
        self._wrapped_collections.clear()
        self._ensured_tables.clear()
        self._ensured_indexes.clear()
        self._ensured_extensions.clear()
        if self._monitor_task and not preserve_monitor:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def connect(self, retries: int = 3, initial_delay: float = 1.0) -> None:
        async with self._connection_lock:
            if self.is_connected:
                return

            attempt = 0
            delay = initial_delay
            while attempt <= retries and not self.is_connected:
                try:
                    logger.info("Connecting to Postgres at {}", self._dsn)
                    self.pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
                    await self._ensure_extensions()
                    await self._ensure_tables(DEFAULT_COLLECTIONS)
                    await self._ensure_indexes(DEFAULT_COLLECTIONS)
                    self.is_connected = True
                    logger.info("Connected to Postgres and storage table ensured")
                    self._start_monitor()
                    return
                except Exception:
                    attempt += 1
                    logger.exception("Failed to connect to Postgres (attempt {})", attempt)
                    self._reset_state()
                    if attempt > retries:
                        break
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)

    def _start_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_connection())

    async def _monitor_connection(self) -> None:
        try:
            while True:
                await asyncio.sleep(5)
                if self.pool is None:
                    await self.connect()
                    continue
                try:
                    async with self.pool.acquire() as conn:
                        await conn.execute("SELECT 1")
                except Exception:
                    logger.warning("Lost connection to Postgres, attempting to reconnect")
                    try:
                        if self.pool:
                            await self.pool.close()
                    except Exception:
                        logger.debug("Error while closing pool during reconnect", exc_info=True)
                    self._reset_state(preserve_monitor=True)
                    await self.connect()
        except asyncio.CancelledError:
            return

    def _table_name_for_collection(self, collection: str) -> str:
        if not collection or not collection.replace("_", "").isalnum():
            raise ValueError(f"Invalid collection name '{collection}'")
        return f"{TABLE_PREFIX}{collection}"

    async def _ensure_extensions(self) -> None:
        if self.pool is None:
            return
        if "pg_trgm" in self._ensured_extensions:
            return
        try:
            await self.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            self._ensured_extensions.add("pg_trgm")
        except Exception:
            logger.warning("Could not ensure pg_trgm extension; trigram index may not be available")

    async def _ensure_tables(self, collections: Iterable[str]) -> None:
        for collection in collections:
            table_name = self._table_name_for_collection(collection)
            await self.ensure_table(table_name)

    async def ensure_table(self, table_name: str) -> None:
        if self.pool is None or table_name in self._ensured_tables:
            return
        await self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id TEXT NOT NULL,
                doc JSONB NOT NULL,
                PRIMARY KEY (id)
            );
            """
        )
        try:
            await self.execute(
                f"ALTER TABLE {table_name} ALTER COLUMN doc TYPE JSONB USING doc::jsonb;"
            )
        except Exception:
            logger.warning("Could not coerce doc column to JSONB for {}", table_name)
        self._ensured_tables.add(table_name)

    async def _ensure_indexes(self, collections: Iterable[str]) -> None:
        for collection in collections:
            await self.ensure_indexes_for_collection(collection)

    async def ensure_indexes_for_collection(self, collection: str) -> None:
        if self.pool is None:
            return
        table_name = self._table_name_for_collection(collection)
        await self.ensure_table(table_name)
        specs = INDEX_SPECS.get(collection, [])
        for suffix, expression in specs:
            index_name = f"{table_name}_{suffix}_idx"
            cache_key = f"{table_name}:{index_name}"
            if cache_key in self._ensured_indexes:
                continue
            await self.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} {expression};")
            self._ensured_indexes.add(cache_key)

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
        table_name = self._table_name_for_collection(name)
        collection = PostgresCollection(name, table_name, self)
        self._wrapped_collections[name] = collection
        return collection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        if self.pool is None:
            raise RuntimeError("Database not connected")
        async with self.pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                yield conn
            except Exception:
                await tx.rollback()
                raise
            else:
                await tx.commit()

    async def execute(self, query: str, *args: Any, conn: Optional[asyncpg.Connection] = None) -> None:
        if self.pool is None and conn is None:
            raise RuntimeError("Database not connected")
        if conn is not None:
            await conn.execute(query, *args)
            return
        pool = self.pool
        if pool is None:
            raise RuntimeError("Database not connected")
        async with pool.acquire() as pooled_conn:
            await pooled_conn.execute(query, *args)

    async def fetch(
        self, query: str, *args: Any, conn: Optional[asyncpg.Connection] = None
    ) -> List[asyncpg.Record]:
        if self.pool is None and conn is None:
            raise RuntimeError("Database not connected")
        if conn is not None:
            return await conn.fetch(query, *args)
        pool = self.pool
        if pool is None:
            raise RuntimeError("Database not connected")
        async with pool.acquire() as pooled_conn:
            return await pooled_conn.fetch(query, *args)

    async def get_pool_stats(self) -> Dict[str, Any]:
        if self.pool is None:
            return {"status": "not_connected"}
        return {
            "status": "connected",
            "pool_size": getattr(self.pool, "get_max_size", lambda: None)(),
            "free": getattr(self.pool, "get_idle_size", lambda: None)(),
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
    logger.info("Ensuring JSONB indexes for default collections")
    await db_instance._ensure_indexes(DEFAULT_COLLECTIONS)


async def init_database() -> None:
    try:
        await db_instance.connect()
        if db_instance.is_connected:
            await create_indexes()
            pool_stats = await db_instance.get_pool_stats()
            logger.info("Database pool stats: {}", pool_stats)
    except Exception:
        logger.error("Error initializing database")
