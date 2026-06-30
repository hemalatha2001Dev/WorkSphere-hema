from contextlib import contextmanager
from app.db.mysql import engine, SessionLocal, Base, get_mysql_session

def get_performance_db():
    """FastAPI Dependency to get MySQL Session (previously SQLite)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def performance_db_session():
    """Context manager to use MySQL Session in normal Python functions"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
