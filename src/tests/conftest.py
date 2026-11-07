"""Test defaults and opt-in PostgreSQL isolation; never reuse application data."""

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.database import db_instance
from src.schema import metadata


@pytest_asyncio.fixture
async def postgres(monkeypatch):
    """Create and remove only a uniquely named schema in an explicit test database."""
    database_url = os.getenv("WIKIWARE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set WIKIWARE_TEST_DATABASE_URL to run PostgreSQL integration tests")
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+asyncpg" or "test" not in (parsed.database or "").lower():
        pytest.fail("WIKIWARE_TEST_DATABASE_URL must use asyncpg and a database named for tests")

    schema = f"wikiware_test_{uuid4().hex}"
    administration = create_async_engine(database_url, poolclass=NullPool)
    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with administration.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        monkeypatch.setattr(db_instance, "engine", engine)
        monkeypatch.setattr(db_instance, "is_connected", True)
        yield engine
    finally:
        await engine.dispose()
        async with administration.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administration.dispose()
