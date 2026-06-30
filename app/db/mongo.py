from pymongo import MongoClient
from app.core.config import settings

def get_mongo_client():
    """
    Get MongoDB client. If production SRV connection fails to initialize 
    (e.g., DNS resolution fails in offline environment), falls back to a 
    standard local connection to prevent app crashes during import.
    """
    try:
        # Try production connection string
        client = MongoClient(settings.MONGO_DB_URL, serverSelectionTimeoutMS=2000)
        # Accessing an attribute doesn't trigger connection, but SRV resolution occurs on init
        return client
    except Exception as e:
        print(f"Warning: Failed to initialize MongoDB with production URL: {e}.")
        print("Falling back to local MongoDB connection to prevent import/startup crashes.")
        try:
            return MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=1000)
        except Exception:
            return None
