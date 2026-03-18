from pydantic import BaseSettings


class Settings(BaseSettings):
    # Security
    SECRET_KEY: str = "supersecretkey"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 5000

    # Database connections
    MYSQL_URL: str = "mysql+pymysql://root:snr%401234@localhost/manager_db"
    MONGO_DB_URL: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "manger_db"  # Keeping your original typo

    # Outlook / Email credentials
    CLIENT_ID: str = ""
    CLIENT_SECRET: str = ""
    TENANT_ID: str = ""
    SENDER_EMAIL: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env variables


settings = Settings()
