from pymongo import MongoClient
from app.core.config import settings

def get_mongo_client():
    # Return MongoClient directly. It connects lazily when needed.
    # This prevents the app from crashing entirely during startup if MongoDB takes a few seconds to connect.
    client = MongoClient(settings.MONGO_DB_URL, serverSelectionTimeoutMS=5000)
    return client
