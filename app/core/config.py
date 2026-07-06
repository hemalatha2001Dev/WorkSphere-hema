import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load env variables from .env if present
load_dotenv()

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "WORKSPACE"
    DEBUG: bool = True

    # Database Settings
    # Fallback to local default values if not defined in .env or system environment variables
    MYSQL_URL: str = "mysql+pymysql://root:worksphere@@2026@127.0.0.1:3306/snr_app_db"
    MONGO_DB_URL: str = "mongodb+srv://devtestkarthik_db_user:JY8ocxjnAl3OTrtz@cluster0.5hkjala.mongodb.net/?appName=Cluster0"
    MONGO_DB_NAME: str = "worksphere"
    
    # Security Settings
    SECRET_KEY: str = "supersecretkey"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # default to 24 hours

    # Email Settings
    SENDER_EMAIL: str = "dammalapatihemalatha2000@gmail.com"
    GMAIL_APP_PASSWORD: str = "gfny oztz xcjh jhba"

    # Cloud Storage Settings
    GCS_BUCKET_NAME: str = "workspehere-bukcet"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
