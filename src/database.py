"""Async PostgreSQL connection lifecycle for WikiWare.

Query and transaction code belongs in services. This module owns only the
shared engine and the lifecycle surface used by application startup code.
"""

import asyncio
import math
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import config

load_dotenv()


class Database:
    """Manage the application's async SQLAlchemy engine."""

    def __init__(self, database_url: str = config.DATABASE_URL):
        self._database_url = database_url
        self.engine: Optional[AsyncEngine] = None
        self.is_connected = False
        self._connection_lock = asyncio.Lock()

    async def _dispose_engine(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
        self.engine = None
        self.is_connected = False

    async def connect(
        self, max_retries: Optional[int] = config.DATABASE_CONNECT_RETRIES
    ) -> None:
        """Connect and validate PostgreSQL, retrying transient failures."""
        attempts = math.inf if max_retries is None else max(1, max_retries)

        async with self._connection_lock:
            attempt = 0
            while attempt < attempts:
                attempt += 1
                try:
                    logger.info(
                        "Attempting to connect to PostgreSQL at {} (attempt {})",
                        self._database_url.split("@")[-1],
                        attempt,
                    )
                    self.engine = create_async_engine(
                        self._database_url,
                        pool_pre_ping=True,
                        pool_size=config.DATABASE_POOL_SIZE,
                        max_overflow=config.DATABASE_MAX_OVERFLOW,
                        pool_timeout=config.DATABASE_POOL_TIMEOUT,
                        pool_recycle=config.DATABASE_POOL_RECYCLE,
                        connect_args={"timeout": config.DATABASE_POOL_TIMEOUT},
                    )
                    async with self.engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                    self.is_connected = True
                    logger.info("Connected to PostgreSQL")
                    return
                except Exception as exc:
                    logger.warning("PostgreSQL connection attempt {} failed: {}", attempt, exc)
                    await self._dispose_engine()
                    if attempt >= attempts:
                        logger.error("Failed to connect to PostgreSQL; running offline")
                        return
                    await asyncio.sleep(min(5, attempt))

    async def monitor_connection(self) -> None:
        """Keep trying to reconnect while the application is running."""
        logger.info("Starting PostgreSQL connection monitor (retries every 10s)")
        while True:
            if not self.is_connected:
                await self.connect(max_retries=1)
            await asyncio.sleep(10)

    async def disconnect(self) -> None:
        """Dispose the engine and reset connection state."""
        async with self._connection_lock:
            await self._dispose_engine()

    async def get_pool_stats(self) -> Dict[str, Any]:
        """Return lightweight pool status for health and startup logging."""
        if self.engine is None or not self.is_connected:
            return {"status": "not_connected"}

        pool = self.engine.pool
        stats: Dict[str, Any] = {"status": "connected"}
        for name in ("size", "checkedin", "checkedout", "overflow"):
            value = getattr(pool, name, None)
            if callable(value):
                stats[name] = value()
        return stats


db_instance = Database()


async def create_indexes() -> None:
    """Compatibility hook; schema indexes are owned by Alembic migrations."""
    if db_instance.is_connected:
        logger.debug("PostgreSQL indexes are managed by Alembic")


async def init_database() -> None:
    """Initialize the shared PostgreSQL connection."""
    try:
        await db_instance.connect()
        if db_instance.is_connected:
            await create_indexes()
            logger.info("Database pool stats: {}", await db_instance.get_pool_stats())
    except Exception:
        logger.exception("Error initializing database")
