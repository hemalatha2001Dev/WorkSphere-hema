import sys
import os

# Add current dir to path to import app settings
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.mongo import get_mongo_client
from app.core.config import settings

def test_connection():
    try:
        client = get_mongo_client()
        if client is None:
            print("Failed to initialize MongoDB client.")
            return

        # Ping the database
        client.admin.command('ping')
        print(f"SUCCESS: Connected to MongoDB at {settings.MONGO_DB_URL}")
        
        # Check specific database
        db = client[settings.MONGO_DB_NAME]
        print(f"Successfully accessed database: {settings.MONGO_DB_NAME}")
        
        # List collections
        collections = db.list_collection_names()
        print(f"Collections in database: {collections}")
        
    except Exception as e:
        print(f"ERROR: Failed to connect to MongoDB.")
        print(f"Details: {str(e)}")

if __name__ == "__main__":
    test_connection()
