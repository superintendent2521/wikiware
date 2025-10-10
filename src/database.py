"""Database connection and management for MongoDB."""

import asyncio
import os
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import ServerSelectionTimeoutError

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "wikiware")

IndexKey = str | Sequence[tuple[str, int | str]]
IndexSpec = tuple[IndexKey, dict[str, Any]]

INDEX_CONFIGS: dict[str, list[IndexSpec]] = {
    "users": [
        ("username", {"unique": True}),
        ("created_at", {}),
    ],
    "sessions": [
        ("session_id", {"unique": True}),
        ("user_id", {}),
        ("expires_at", {"expireAfterSeconds": 0}),
    ],
    "image_hashes": [
        ("filename", {"unique": True}),
        ("sha256", {}),
    ],
}


class Database:
    """Manages MongoDB connection and provides access to collections."""

    def __init__(self, mongo_url: str = MONGODB_URL, db_name: str = MONGODB_DB_NAME):
        self._mongo_url = mongo_url
        self._db_name = db_name
        self.client: AsyncIOMotorClient | None = None # pyright: ignore[reportInvalidTypeForm]
        self.db: AsyncIOMotorDatabase | None = None # pyright: ignore[reportInvalidTypeForm]
        self.is_connected = False

    def _reset_state(self) -> None:
        """Clear cached client/db handles and connection flags."""
        if self.client is not None:
            self.client.close()
        self.client = None
        self.db = None
        self.is_connected = False

    async def connect(self, max_retries: int | None = 10) -> None:
        """Establish connection to MongoDB and test connectivity with retry logic."""
        if max_retries is None:
            max_retries = float("inf")  # Infinite retries for background monitor
            delay = 10  # 10 seconds for ongoing retries
        else:
            delay = 5  # 5 seconds for startup

        retry_count = 0

        while retry_count < max_retries:
            try:
                logger.info(
                    "Attempting to connect to MongoDB at {}... (attempt {})",
                    self._mongo_url,
                    retry_count + 1,
                )
                self.client = AsyncIOMotorClient(
                    self._mongo_url, serverSelectionTimeoutMS=10000
                )
                # Test the connection
                await self.client.admin.command("ping")
                self.db = self.client[self._db_name]
                self.is_connected = True
                logger.info("Connected to MongoDB database '{}'", self._db_name)
                return  # Exit the loop on successful connection
            except ServerSelectionTimeoutError:
                retry_count += 1
                self._reset_state()
                if max_retries != float("inf"):
                    logger.warning(
                        "MongoDB server not available. Attempt {}/{}. Retrying in {} seconds...",
                        retry_count,
                        max_retries,
                        delay,
                    )
                else:
                    logger.warning(
                        "MongoDB connection lost. Retrying in {} seconds...", delay
                    )
                await asyncio.sleep(delay)
            except Exception as exc:  # IGNORE W0718
                logger.error("Database connection error: {}", exc)
                self._reset_state()
                if max_retries != float("inf"):
                    return  # Don't retry on other errors for startup
                await asyncio.sleep(delay)  # Retry on errors for background

        # If we've exhausted all retries (startup only)
        if max_retries != float("inf"):
            logger.error(
                "Failed to connect to MongoDB after multiple attempts. Running in offline mode."
            )
            self._reset_state()

    async def monitor_connection(self) -> None:
        """Background task to monitor and retry connection if lost."""
        logger.info("Starting MongoDB connection monitor (retries every 10s)")
        while True:
            if not self.is_connected:
                await self.connect(max_retries=None)  # Infinite retry
            await asyncio.sleep(10)  # Check every 10 seconds even if connected

    async def disconnect(self) -> None:
        """Close the MongoDB connection."""
        self._reset_state()

    def get_collection(self, name: str) -> AsyncIOMotorCollection | None: # pyright: ignore[reportInvalidTypeForm]
        """Get a collection by name if database is connected."""
        if self.is_connected and self.db is not None:
            return self.db[name]
        return None


# Global database instance
db_instance = Database()


# Collections
def get_pages_collection():
    """Get the pages collection."""
    return db_instance.get_collection("pages")


def get_history_collection():
    """Get the history collection."""
    return db_instance.get_collection("history")


def get_branches_collection():
    """Get the branches collection."""
    return db_instance.get_collection("branches")


def get_users_collection():
    """Get the users collection."""
    return db_instance.get_collection("users")


def get_image_hashes_collection():
    """Get the image_hashes collection."""
    return db_instance.get_collection("image_hashes")


# Helper functions
async def _drop_legacy_page_index(pages: AsyncIOMotorCollection) -> None: # pyright: ignore[reportInvalidTypeForm]
    """Remove deprecated single-field title index if present."""
    try:
        existing_indexes = await pages.index_information()
        if "title_1" in existing_indexes:
            await pages.drop_index("title_1")
            logger.info("Dropped legacy unique index on pages.title")
    except Exception as exc:  # IGNORE W0718
        logger.warning("Failed to drop legacy title index: {}", exc)


async def _ensure_pages_indexes(pages: AsyncIOMotorCollection) -> None: # pyright: ignore[reportInvalidTypeForm]
    """Ensure compound and text indexes exist for the pages collection."""
    await _drop_legacy_page_index(pages)
    await pages.create_index([("title", 1), ("branch", 1)], unique=True)
    await pages.create_index("updated_at")
    await pages.create_index(
        [("title", "text"), ("content", "text")], name="page_text_search"
    )
    logger.info("Pages collection indexes created")


async def _create_collection_indexes(
    collection_name: str, collection: AsyncIOMotorCollection # pyright: ignore[reportInvalidTypeForm]
) -> None:
    """Create indexes declared in INDEX_CONFIGS for a given collection."""
    for keys, options in INDEX_CONFIGS.get(collection_name, []):
        await collection.create_index(keys, **options)
    logger.info("{} collection indexes created", collection_name.capitalize())


async def create_indexes() -> None:
    """Create required indexes on MongoDB collections."""
    if not db_instance.is_connected:
        logger.warning("Skipping index creation because database is disconnected")
        return

    pages = get_pages_collection()
    if pages is not None:
        await _ensure_pages_indexes(pages)

    for collection_name in ("users", "sessions", "image_hashes"):
        collection = db_instance.get_collection(collection_name)
        if collection is None:
            logger.warning(
                "Collection '{}' unavailable while creating indexes", collection_name
            )
            continue
        await _create_collection_indexes(collection_name, collection)


async def init_database() -> None:
    """Initialize database connection and create indexes."""
    try:
        await db_instance.connect()  # Finite retries for startup
        if db_instance.is_connected:
            await create_indexes()
    except Exception as exc:  # IGNORE W0718
        logger.error("Error initializing database: {}", exc)
