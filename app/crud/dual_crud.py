import re
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.mysql_models import User as MySQLUser
from app.db.mysql import get_mysql_session, SessionLocal
from app.db.mongo import get_mongo_client
from app.core.config import settings

def normalize_custom_fields(custom_fields):
    """
    Ensures custom_fields is always a list of {name, value} dicts.
    """
    if isinstance(custom_fields, dict):
        return [{"name": str(k), "value": str(v)} for k, v in custom_fields.items()]
    elif isinstance(custom_fields, list):
        normalized = []
        for item in custom_fields:
            if isinstance(item, dict) and "name" in item and "value" in item:
                normalized.append({
                    "name": str(item.get("name")),
                    "value": str(item.get("value"))
                })
        return normalized
    return []


def get_user_by_identifier(identifier: str):
    """
    MongoDB as PRIMARY - Try MongoDB first, MySQL as fallback
    """
    is_email = bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))

    # Try MongoDB first (PRIMARY)
    mongo_client = get_mongo_client()
    if mongo_client:
        field = "email" if is_email else "username"
        mongo_user = mongo_client[settings.MONGO_DB_NAME]["users"].find_one({field: identifier})
        if mongo_user:
            mongo_user["id"] = str(mongo_user["_id"])
            mongo_user["custom_fields"] = normalize_custom_fields(mongo_user.get("custom_fields"))
            return mongo_user

    # Fallback to MySQL (SECONDARY)
    db = None
    try:
        db = next(get_mysql_session())
        query = MySQLUser.email == identifier if is_email else MySQLUser.username == identifier
        user = db.query(MySQLUser).filter(query).first()
        if user:
            user_dict = {k: v for k, v in user.__dict__.items() if not k.startswith("_")}
            user_dict["custom_fields"] = normalize_custom_fields(user_dict.get("custom_fields"))
            return user_dict
    except SQLAlchemyError as e:
        print(f"MySQL error: {e}")
    finally:
        if db:
            db.close()

    return None



def update_user_password(email: str, hashed_password: str) -> bool:
    """
    Updates password in MongoDB (PRIMARY) first, then MySQL (fallback)
    """
    # Update MongoDB PRIMARY
    try:
        mongo_client = get_mongo_client()
        if mongo_client:
            result = mongo_client[settings.MONGO_DB_NAME]["users"].update_one(
                {"email": email},
                {"$set": {"password": hashed_password}}
            )
            if result.modified_count == 0:
                print("User not found in MongoDB primary")
                return False
        else:
            print("MongoDB primary connection failed.")
            return False
    except Exception as e:
        print(f"MongoDB primary error: {e}")
        return False

    # Update MySQL FALLBACK
    db = None
    try:
        db = SessionLocal()
        user = db.query(MySQLUser).filter(MySQLUser.email == email).first()
        if user:
            user.password = hashed_password
            db.commit()
    except SQLAlchemyError as e:
        print(f"MySQL fallback error: {e}")
        # Don't return False here since primary succeeded
    finally:
        if db:
            db.close()

    return True
