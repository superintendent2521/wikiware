import os
import asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError
from loguru import logger

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

"""Database connection and management for MongoDB."""


class Database:
    """Manages MongoDB connection and provides access to collections."""

    def __init__(self):
        self.client = None
        self.db = None
        self.is_connected = False

    async def connect(self, max_retries: int | None = 10):
        """Establish connection to MongoDB and test connectivity with retry logic."""
        if max_retries is None:
            max_retries = float('inf')  # Infinite retries for background monitor
            delay = 10  # 10 seconds for ongoing retries
        else:
            delay = 5  # 5 seconds for startup

        retry_count = 0

        while retry_count < max_retries:
            try:
                logger.info(f"Attempting to connect to MongoDB at {MONGODB_URL}... (attempt {retry_count + 1})")
                self.client = AsyncIOMotorClient(
                    MONGODB_URL, serverSelectionTimeoutMS=10000
                )
                # Test the connection
                await self.client.admin.command("ping")
                self.db = self.client.wikiware
                self.is_connected = True
                logger.info("Connected to MongoDB successfully")
                return  # Exit the loop on successful connection
            except ServerSelectionTimeoutError:
                retry_count += 1
                if max_retries != float('inf'):
                    logger.warning(
                        f"MongoDB server not available. Attempt {retry_count}/{max_retries}."
                        f"Retrying in {delay} seconds... Server Offline?"
                    )
                else:
                    logger.warning(
                        f"MongoDB connection lost. Retrying in {delay} seconds..."
                    )
                await asyncio.sleep(delay)
            except Exception as e:  # IGNORE W0718
                logger.error(f"Database connection error: {e}")
                self.is_connected = False
                if max_retries != float('inf'):
                    return  # Don't retry on other errors for startup
                else:
                    await asyncio.sleep(delay)  # Retry on errors for background

        # If we've exhausted all retries (startup only)
        if max_retries != float('inf'):
            logger.error(
                "Failed to connect to MongoDB after multiple attempts. Running in offline mode."
            )
            self.is_connected = False

    async def monitor_connection(self):
        """Background task to monitor and retry connection if lost."""
        logger.info("Starting MongoDB connection monitor (retries every 10s)")
        while True:
            if not self.is_connected:
                await self.connect(max_retries=None)  # Infinite retry
            await asyncio.sleep(10)  # Check every 10 seconds even if connected

    async def disconnect(self):
        """Close the MongoDB connection."""
        if self.client:
            self.client.close()

    def get_collection(self, name):
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
async def create_indexes():
    """Create required indexes on MongoDB collections."""
    if db_instance.is_connected:
        # Create indexes for pages collection
        pages = get_pages_collection()
        if pages is not None:
            # Drop the old unique index on title alone if it exists
            try:
                await pages.drop_index("title_1")
                logger.info("Dropped old unique index on title")
            except Exception as e:
                logger.warning(f"Failed to drop old index on title: {str(e)}")
                pass  # Index might not exist, that's fine

            # Create compound unique index on title and branch
            await pages.create_index([("title", 1), ("branch", 1)], unique=True)
            await pages.create_index("updated_at")
            await pages.create_index(
                [("title", "text"), ("content", "text")], name="page_text_search"
            )
            logger.info("Pages collection indexes created")

        # Create indexes for users collection
        users = get_users_collection()
        if users is not None:
            await users.create_index("username", unique=True)
            await users.create_index("created_at")
            logger.info("Users collection indexes created")

        # Create indexes for sessions collection
        sessions = db_instance.get_collection("sessions")
        if sessions is not None:
            await sessions.create_index("session_id", unique=True)
            await sessions.create_index("user_id")
            await sessions.create_index("expires_at", expireAfterSeconds=0)
            logger.info("Sessions collection indexes created")

        # Create indexes for image_hashes collection
        image_hashes = get_image_hashes_collection()
        if image_hashes is not None:
            await image_hashes.create_index("filename", unique=True)
            await image_hashes.create_index("sha256")
            logger.info("Image hashes collection indexes created")


async def init_database():
    """Initialize database connection and create indexes."""
    try:
        await db_instance.connect()  # Finite retries for startup
        if db_instance.is_connected:
            await create_indexes()
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
