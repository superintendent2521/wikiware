#!/usr/bin/env python3
"""
MongoDB -> Postgres migration helper for WikiWare.

Steps:
1) Creates a mongodump backup (gzip archive) before touching data.
2) Reads MongoDB collections and writes them into Postgres JSONB storage
   using one table per collection (tables prefixed with wikiware_).
3) Upserts documents so the script can be run multiple times.

Usage:
    python scripts/mongo_to_postgres_migration.py [--skip-backup] [--truncate]

Flags:
    --skip-backup   Do not run mongodump first (not recommended).
    --truncate      Delete existing rows for the migrated collections before insert.

Environment:
    MONGODB_URL       (default: mongodb://localhost:27017)
    MONGODB_DB_NAME   (default: wikiware)
    POSTGRES_DSN      (default: postgresql://postgres:postgres@localhost:5432/wikiware)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable

import asyncpg
from dotenv import load_dotenv
from loguru import logger
from pymongo import MongoClient
from bson import ObjectId
from decimal import Decimal

TABLE_PREFIX = "wikiware_"
COLLECTIONS = [
    "pages",
    "history",
    "branches",
    "users",
    "sessions",
    "image_hashes",
    "analytics_events",
    "settings",
    "system_logs",
    "edit_sessions",
]


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def run_backup(uri_with_db: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive = backup_dir / f"mongodb-{_timestamp()}.archive.gz"
    logger.info("Running mongodump to {}", archive)
    cmd = [
        "mongodump",
        f"--uri={uri_with_db}",
        f"--archive={archive}",
        "--gzip",
    ]
    subprocess.run(cmd, check=True)
    logger.info("Backup complete: {}", archive)
    return archive


def _uri_with_db(uri: str, db: str) -> str:
    if uri.rstrip("/").endswith(f"/{db}"):
        return uri
    if "?" in uri:
        base, query = uri.split("?", 1)
        return f"{base.rstrip('/')}/{db}?{query}"
    return f"{uri.rstrip('/')}/{db}"


def load_env_defaults() -> Dict[str, str]:
    load_dotenv()
    return {
        "MONGODB_URL": os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
        "MONGODB_DB_NAME": os.getenv("MONGODB_DB_NAME", "wikiware"),
        "POSTGRES_DSN": os.getenv(
            "POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/wikiware"
        ),
    }


def table_name_for(collection: str) -> str:
    if not collection or not collection.replace("_", "").isalnum():
        raise ValueError(f"Invalid collection name '{collection}'")
    return f"{TABLE_PREFIX}{collection}"


async def ensure_table(pool: asyncpg.Pool, collection: str) -> None:
    table_name = table_name_for(collection)
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id TEXT NOT NULL,
                doc JSONB NOT NULL,
                PRIMARY KEY (id)
            );
            """
        )


def fetch_collection(mongo_db, name: str) -> Iterable[Dict]:
    logger.info("Fetching Mongo collection '{}'", name)
    return mongo_db[name].find()


def _jsonable(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
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


def sanitize_document(doc: Dict) -> Dict:
    cleaned = _jsonable(doc)
    if isinstance(cleaned, dict):
        return cleaned
    return {}


async def upsert_documents(
    pool: asyncpg.Pool, collection: str, documents: Iterable[Dict]
) -> int:
    table_name = table_name_for(collection)
    records_to_insert = []
    for doc in documents:
        doc = dict(doc)
        doc_id = str(doc.get("_id") or doc.get("id") or doc.get("uuid") or "")
        if not doc_id:
            continue
        clean_doc = sanitize_document(doc)
        clean_doc["_id"] = doc_id
        json_payload = json.dumps(clean_doc, ensure_ascii=False)
        records_to_insert.append((doc_id, json_payload))

    if not records_to_insert:
        return 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            stmt = await conn.prepare(
                f"""
                INSERT INTO {table_name} (id, doc)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc
                """
            )
            await stmt.executemany(records_to_insert)

    return len(records_to_insert)


async def migrate_collections(
    mongo_uri: str,
    mongo_db_name: str,
    postgres_dsn: str,
    *,
    backup_first: bool,
    truncate: bool,
) -> None:
    mongo_uri_with_db = _uri_with_db(mongo_uri, mongo_db_name)
    backup_dir = Path("backups")

    if backup_first:
        run_backup(mongo_uri_with_db, backup_dir)
    else:
        logger.warning("Skipping mongodump backup as requested.")

    mongo_client = MongoClient(mongo_uri_with_db)
    mongo_db = mongo_client.get_database()

    pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=10)
    for name in COLLECTIONS:
        await ensure_table(pool, name)

    if truncate:
        async with pool.acquire() as conn:
            logger.info("Truncating existing rows for selected collections")
            for name in COLLECTIONS:
                table_name = table_name_for(name)
                await conn.execute(f"DELETE FROM {table_name}")

    for name in COLLECTIONS:
        documents = fetch_collection(mongo_db, name)
        count = await upsert_documents(pool, name, documents)
        logger.info("Migrated {} documents into '{}'", count, name)

    await pool.close()
    mongo_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate MongoDB data into Postgres JSONB storage."
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not run mongodump before migrating.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete existing rows for migrated collections first.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env_defaults()

    logger.info("Starting Mongo -> Postgres migration")
    logger.info("Mongo URI: {}", env["MONGODB_URL"])
    logger.info("Mongo DB: {}", env["MONGODB_DB_NAME"])
    logger.info("Postgres DSN: {}", env["POSTGRES_DSN"])

    try:
        asyncio.run(
            migrate_collections(
                env["MONGODB_URL"],
                env["MONGODB_DB_NAME"],
                env["POSTGRES_DSN"],
                backup_first=not args.skip_backup,
                truncate=args.truncate,
            )
        )
    except subprocess.CalledProcessError as exc:
        logger.error("Backup failed: {}", exc)
        return 1
    except Exception as exc:
        logger.exception("Migration failed: {}", exc)
        return 1

    logger.info("Migration completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
