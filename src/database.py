import os
import asyncio
import subprocess
from datetime import datetime
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


async def create_backup():
    """Create a database backup using mongodump and save to backups folder."""
    try:
        # Check if mongodump is available
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["mongodump", "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.error("mongodump command not found. Please install MongoDB tools.")
            return {"success": False, "error": "mongodump not available. Install MongoDB tools to enable backups."}

        # Ensure backups directory exists
        backups_dir = "backups"
        os.makedirs(backups_dir, exist_ok=True)

        # Generate timestamped filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{timestamp}.gz"
        filepath = os.path.join(backups_dir, filename)

        # Parse MongoDB URI to extract host, port, db name
        from urllib.parse import urlparse
        parsed_url = urlparse(MONGODB_URL)

        # Determine target connection parameters
        host = parsed_url.hostname or "localhost"
        port = parsed_url.port or 27017

        # Build mongodump command arguments for single archive
        cmd_args = [
            "mongodump",
            "--db", "wikiware",  # Database name
            f"--archive={filepath}",  # Single compressed archive written to file
            "--gzip",  # Compress the archive
            "--host", host,
            "--port", str(port),
        ]

        # Add authentication if present
        if parsed_url.username and parsed_url.password:
            cmd_args.extend(["--username", parsed_url.username, "--password", parsed_url.password])

        # Run mongodump in subprocess
        logger.info(f"Starting database backup: {filename}")
        result = await asyncio.to_thread(
            subprocess.run,
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode == 0:
            logger.info(f"Database backup completed: {filename}")
            return {"success": True, "filename": filename, "path": filepath}
        else:
            error_msg = (result.stderr or "").strip()
            logger.error(f"Backup failed: {error_msg}")
            return {"success": False, "error": f"mongodump failed: {error_msg}"}

    except Exception as e:
        logger.error(f"Backup creation error: {str(e)}")
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


def list_backups():
    """List all backup files with metadata."""
    try:
        backups_dir = "backups"
        if not os.path.exists(backups_dir):
            return []

        backups = []
        for filename in os.listdir(backups_dir):
            if filename.startswith("backup_") and filename.endswith(".gz"):
                filepath = os.path.join(backups_dir, filename)
                stat = os.stat(filepath)
                backups.append({
                    "filename": filename,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "created_at": datetime.fromtimestamp(stat.st_mtime),
                    "path": filepath
                })

        # Sort by creation time, newest first
        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return backups

    except Exception as e:
        logger.error(f"Error listing backups: {e}")
        return []
