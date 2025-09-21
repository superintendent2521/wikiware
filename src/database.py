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

    async def connect(self):
        """Establish connection to MongoDB and test connectivity with retry logic."""
        max_retries = 10  # Will try for ~50 seconds total (10 attempts * 5s delay)
        retry_count = 0

        while retry_count < max_retries:
            try:
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
                logger.warning(
                    f"MongoDB server not available. Attempt {retry_count}/{max_retries}."
                    "Retrying in 5 seconds... Server Offline?"
                )
                await asyncio.sleep(5)  # Wait 5 seconds before retrying
            except Exception as e:  # IGNORE W0718
                logger.error(f"Database connection error: {e}")
                self.is_connected = False
                return  # Don't retry on other errors

        # If we've exhausted all retries
        logger.error(
            "Failed to connect to MongoDB after multiple attempts. Running in offline mode."
        )
        self.is_connected = False

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


async def init_database():
    """Initialize database connection and create indexes."""
    try:
        await db_instance.connect()
        if db_instance.is_connected:
            await create_indexes()
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
