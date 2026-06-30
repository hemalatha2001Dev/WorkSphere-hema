# app/db/mysql.py
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from app.core.config import settings

# MySQL connection URL from settings
SQLALCHEMY_DATABASE_URL = settings.MYSQL_URL

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"charset": "utf8mb4"}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_mysql_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def mysql_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
