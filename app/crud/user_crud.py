from app.models.mysql_models import User as MySQLUser
from app.db.mysql import SessionLocal, mysql_session
from app.db.mongo import get_mongo_client
from app.core.config import settings
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

# Create User in MySQL
def create_user_mysql(user_data: dict):
    try:
        with mysql_session() as session:
            user = MySQLUser(**user_data)
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    except IntegrityError as e:
        error_msg = str(e.orig).lower()
        if "duplicate entry" in error_msg:
            if "username" in error_msg:
                raise HTTPException(status_code=400, detail="Username already exists")
            elif "email" in error_msg:
                raise HTTPException(status_code=400, detail="Email already exists")
            else:
                raise HTTPException(status_code=400, detail="Duplicate value exists")
        raise HTTPException(status_code=500, detail="Database integrity error")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected database error: {str(e)}")


# Create User in MongoDB
def create_user_mongo(user_data: dict) -> bool:
    try:
        client = get_mongo_client()
        if client:
            db = client[settings.MONGO_DB_NAME]
            collection = db["users"]
            collection.insert_one(user_data)
            return True
    except Exception as e:
        print(f"MongoDB insert error: {e}")
    return False


# Get User by Username (Case-insensitive)
def get_user_by_username(username: str):
    username_lower = username.lower()

    # Try MongoDB first
    try:
        client = get_mongo_client()
        if client:
            user = client[settings.MONGO_DB_NAME]["users"].find_one(
                {"username": {"$regex": f"^{username_lower}$", "$options": "i"}},
                {"_id": 0}
            )
            if user:
                user.pop("password", None)
                return user
    except Exception as e:
        print(f"MongoDB fetch error: {e}")

    # Fallback to MySQL
    with SessionLocal() as db:
        user = db.query(MySQLUser).filter(MySQLUser.username.ilike(username_lower)).first()
        if user:
            user_dict = {k: v for k, v in user.__dict__.items() if not k.startswith("_")}
            user_dict.pop("password", None)
            return user_dict

    return None


# Get Password Hash by Username (Case-insensitive)
def get_user_password(username: str):
    username_lower = username.lower()
    with SessionLocal() as db:
        user = db.query(MySQLUser).filter(MySQLUser.username.ilike(username_lower)).first()
        return user.password if user else None
