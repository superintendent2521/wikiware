"""Small async SQLAlchemy Core helpers shared by supporting services."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text

from ..database import db_instance


def _engine():
    """Return the async engine exposed by the database manager."""
    return getattr(db_instance, "engine", None) or getattr(
        db_instance, "async_engine", None
    )


async def fetch_one(
    statement: str, parameters: Optional[Mapping[str, Any]] = None
) -> Optional[dict[str, Any]]:
    """Execute a select and return one mapping, or None when unavailable."""
    engine = _engine()
    if engine is None:
        return None
    async with engine.connect() as connection:
        result = await connection.execute(text(statement), parameters or {})
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def fetch_all(
    statement: str, parameters: Optional[Mapping[str, Any]] = None
) -> list[dict[str, Any]]:
    """Execute a select and return mapping rows."""
    engine = _engine()
    if engine is None:
        return []
    async with engine.connect() as connection:
        result = await connection.execute(text(statement), parameters or {})
        return [dict(row) for row in result.mappings().all()]


async def execute_returning(
    statement: str, parameters: Optional[Mapping[str, Any]] = None
) -> Optional[dict[str, Any]]:
    """Commit a write with RETURNING before handing its row to the caller."""
    engine = _engine()
    if engine is None:
        return None
    async with engine.begin() as connection:
        row = (await connection.execute(text(statement), parameters or {})).mappings().first()
        return dict(row) if row is not None else None


async def execute(
    statement: str,
    parameters: Optional[Mapping[str, Any]] = None,
    many: Optional[Sequence[Mapping[str, Any]]] = None,
) -> int:
    """Execute a write in a transaction and return affected row count."""
    engine = _engine()
    if engine is None:
        return 0
    async with engine.begin() as connection:
        result = await connection.execute(
            text(statement), many if many is not None else parameters or {}
        )
        return result.rowcount if result.rowcount is not None else 0
