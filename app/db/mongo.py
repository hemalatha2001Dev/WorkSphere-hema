from pymongo import MongoClient
from app.core.config import settings

def get_mongo_client():
    try:
        client = MongoClient(settings.MONGO_DB_URL, serverSelectionTimeoutMS=3000)
        client.server_info()  
        return client
    except Exception as e:
        print(f"[MongoDB] Connection failed: {e}")
        return None
