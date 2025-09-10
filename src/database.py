from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

class Database:
    def __init__(self):
        self.client = None
        self.db = None
        self.is_connected = False

    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(MONGODB_URL, serverSelectionTimeoutMS=10000)
            # Test the connection
            await self.client.admin.command('ping')
            self.db = self.client.wikiware
            self.is_connected = True
            print("Connected to MongoDB successfully")
        except ServerSelectionTimeoutError:
            print("Warning: MongoDB server not available. Running in offline mode.")
            self.is_connected = False
        except Exception as e:
            print(f"Database connection error: {e}")
            print(f"MongoDB URL: {MONGODB_URL}")
            self.is_connected = False

    async def disconnect(self):
        if self.client:
            self.client.close()

    def get_collection(self, name):
        if self.is_connected and self.db is not None:
            return self.db[name]
        return None

# Global database instance
db_instance = Database()

# Collections
def get_pages_collection():
    return db_instance.get_collection("pages")

def get_history_collection():
    return db_instance.get_collection("history")

# Helper functions
async def create_indexes():
    if db_instance.is_connected:
        pages = get_pages_collection()
        if pages is not None:
            await pages.create_index("title", unique=True)
            await pages.create_index("updated_at")
            print("Database indexes created")

async def init_database():
    await db_instance.connect()
    if db_instance.is_connected:
        await create_indexes()
