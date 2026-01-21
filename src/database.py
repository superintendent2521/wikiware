"""Database connection and management for MongoDB."""

import asyncio
import inspect
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)
from pymongo.errors import ServerSelectionTimeoutError

from . import config

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "wikiware")
MONGODB_MAX_CONNECTIONS = int(os.getenv("MONGODB_MAX_CONNECTIONS", "100"))
MONGODB_MIN_CONNECTIONS = int(os.getenv("MONGODB_MIN_CONNECTIONS", "10"))
MONGODB_MAX_IDLE_TIME_MS = int(os.getenv("MONGODB_MAX_IDLE_TIME_MS", "30000"))
MONGODB_SERVER_SELECTION_TIMEOUT_MS = int(
    os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "5000")
)
MONGODB_SOCKET_TIMEOUT_MS = int(os.getenv("MONGODB_SOCKET_TIMEOUT_MS", "30000"))
DB_OPERATION_LOG_THRESHOLD_MS = (
    float(os.getenv("DB_OPERATION_LOG_THRESHOLD_MS", "100")) / 1000
)  # Convert to seconds

IndexKey = str
IndexSpec = tuple[IndexKey, Dict[str, Any]]

INDEX_CONFIGS: Dict[str, List[IndexSpec]] = {
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
    "analytics_events": [
        ([("event_type", 1), ("timestamp", -1)], {}),
        ("timestamp", {}),
        ("query_normalized", {}),
    ],
}


def _timed_wrapper(original_method, method_name, collection_name):
    """Wrap collection methods with timing logs while preserving their sync/async nature."""
    if inspect.iscoroutinefunction(original_method):

        async def timed_method(*args, **kwargs):
            start_time = time.monotonic()
            try:
                result = await original_method(*args, **kwargs)
                duration = time.monotonic() - start_time
                if (
                    config.DB_QUERY_LOGGING_ENABLED
                    and duration >= DB_OPERATION_LOG_THRESHOLD_MS
                ):
                    logger.info(
                        f"DB {method_name} on {collection_name} took {duration:.4f}s"
                    )
                return result
            except Exception as e:
                duration = time.monotonic() - start_time
                logger.error(
                    f"DB {method_name} on {collection_name} failed after {duration:.4f}s: {e}"
                )
                raise

        return timed_method

    def timed_method(*args, **kwargs):
        start_time = time.monotonic()
        try:
            result = original_method(*args, **kwargs)
            duration = time.monotonic() - start_time
            if (
                config.DB_QUERY_LOGGING_ENABLED
                and duration >= DB_OPERATION_LOG_THRESHOLD_MS
            ):
                logger.info(
                    f"DB {method_name} on {collection_name} took {duration:.4f}s"
                )
            return result
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(
                f"DB {method_name} on {collection_name} failed after {duration:.4f}s: {e}"
            )
            raise

    return timed_method


class Database:
    """Manages MongoDB connection and provides access to collections."""

    def __init__(self, mongo_url: str = MONGODB_URL, db_name: str = MONGODB_DB_NAME):
        self._mongo_url = mongo_url
        self._db_name = db_name
        self.client: Optional[AsyncIOMotorClient] = None  # type: ignore
        self.db: Optional[AsyncIOMotorDatabase] = None  # type: ignore
        self.is_connected = False
        self._connection_lock = asyncio.Lock()
        self._max_pool_size = MONGODB_MAX_CONNECTIONS
        self._min_pool_size = MONGODB_MIN_CONNECTIONS
        self._wrapped_collections: Dict[str, AsyncIOMotorCollection] = {}
        # List of methods to wrap with timing
        self._methods_to_wrap = [
            "find_one",
            "insert_one",
            "update_one",
            "delete_one",
            "find",
            "count_documents",
            "aggregate",
            "distinct",
        ]

    def _reset_state(self) -> None:
        """Clear cached client/db handles and connection flags."""
        if self.client is not None:
            self.client.close()
        self.client = None
        self.db = None
        self.is_connected = False
        self._wrapped_collections.clear()

    async def connect(self, max_retries: Optional[int] = 10) -> None:
        """Establish connection to MongoDB with connection pooling and retry logic."""
        if max_retries is None:
            max_retries = float("inf")
            delay = 10
        else:
            delay = 5

        retry_count = 0

        async with self._connection_lock:
            while retry_count < max_retries:
                try:
                    logger.info(
                        "Attempting to connect to MongoDB at {}... (attempt {}) with pool_size={}, min_pool_size={}",
                        self._mongo_url,
                        retry_count + 1,
                        self._max_pool_size,
                        self._min_pool_size,
                    )

                    self.client = AsyncIOMotorClient(
                        self._mongo_url,
                        maxPoolSize=self._max_pool_size,
                        minPoolSize=self._min_pool_size,
                        maxIdleTimeMS=MONGODB_MAX_IDLE_TIME_MS,
                        serverSelectionTimeoutMS=MONGODB_SERVER_SELECTION_TIMEOUT_MS,
                        socketTimeoutMS=MONGODB_SOCKET_TIMEOUT_MS,
                        retryWrites=True,
                        retryReads=True,
                        connectTimeoutMS=MONGODB_SERVER_SELECTION_TIMEOUT_MS,
                    )

                    # Test the connection
                    await self.client.admin.command("ping")
                    self.db = self.client[self._db_name]
                    self.is_connected = True
                    logger.info(
                        "Connected to MongoDB database '{}' with connection pool configured",
                        self._db_name,
                    )
                    return
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
                except Exception:
                    logger.exception("Database connection error")
                    self._reset_state()
                    if max_retries != float("inf"):
                        return
                    await asyncio.sleep(delay)

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
                await self.connect(max_retries=None)
            await asyncio.sleep(10)

    async def disconnect(self) -> None:
        """Close the MongoDB connection."""
        async with self._connection_lock:
            self._reset_state()

    def get_collection(
        self, name: str
    ) -> Optional[AsyncIOMotorCollection]:  # pyright: ignore[reportInvalidTypeForm]
        """Get a collection by name if database is connected."""
        if self.is_connected and self.db is not None:
            if name in self._wrapped_collections:
                return self._wrapped_collections[name]

            collection = self.db[name]
            # Wrap methods for timing once per collection
            for method_name in self._methods_to_wrap:
                if hasattr(collection, method_name):
                    original_method = getattr(collection, method_name)
                    if getattr(original_method, "_wikiware_timed", False):
                        continue
                    wrapped_method = _timed_wrapper(original_method, method_name, name)
                    setattr(wrapped_method, "_wikiware_timed", True)
                    setattr(collection, method_name, wrapped_method)
            self._wrapped_collections[name] = collection
            return collection
        return None

    async def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics."""
        if self.client is None:
            return {"status": "not_connected"}

        try:
            server_info = await self.client.server_info()
            return {
                "status": "connected",
                "max_pool_size": self._max_pool_size,
                "min_pool_size": self._min_pool_size,
                "server_info": server_info,
            }
        except Exception:
            logger.error("Error getting pool stats")
            return {"status": "error", "error": "Failed to get pool stats"}


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
async def _drop_legacy_page_index(pages: AsyncIOMotorCollection) -> None:  # type: ignore
    """Remove deprecated single-field title index if present."""
    try:
        existing_indexes = await pages.index_information()
        if "title_1" in existing_indexes:
            await pages.drop_index("title_1")
            logger.info("Dropped legacy unique index on pages.title")
    except Exception:
        logger.warning("Failed to drop legacy title index")


async def _ensure_pages_indexes(pages: AsyncIOMotorCollection) -> None:  # type: ignore
    """Ensure compound and text indexes exist for the pages collection."""
    await _drop_legacy_page_index(pages)
    await pages.create_index([("title", 1), ("branch", 1)], unique=True)
    await pages.create_index("updated_at")
    await pages.create_index(
        [("title", "text"), ("content", "text")], name="page_text_search"
    )
    logger.info("Pages collection indexes created")


async def _create_collection_indexes(
    collection_name: str, collection: AsyncIOMotorCollection  # type: ignore
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

    for collection_name in ("users", "sessions", "image_hashes", "analytics_events"):
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
        await db_instance.connect()
        if db_instance.is_connected:
            await create_indexes()
            # Log connection pool stats
            pool_stats = await db_instance.get_pool_stats()
            logger.info("Database pool stats: {}", pool_stats)
    except Exception:
        logger.error("Error initializing database")
