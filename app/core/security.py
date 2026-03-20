from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM

# OAuth2 scheme for FastAPI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")


def hash_password(password: str) -> str:
    """Hash a plain text password using passlib."""
    if not password:
        return ""
    # Explicitly truncate for bcrypt legacy limit if needed, 
    # though passlib usually handles this, direct truncation is safer for cross-platform.
    safe_password = str(password)[:72]
    return pwd_context.hash(safe_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against its hash with robust error handling."""
    if not plain_password or not hashed_password:
        return False
    try:
        # Bcrypt legacy limit is 72 chars. Truncate to avoid 500 errors on Cloud Run.
        safe_password = str(plain_password)[:72]
        return pwd_context.verify(safe_password, hashed_password)
    except Exception as e:
        print(f"Password verification error: {e}")
        # If verification fails due to format mismatch, return False instead of crashing
        return False


def create_access_token(data: dict, expires_delta: timedelta) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Decode JWT and return user payload.
    Raises HTTP 401 if token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str = payload.get("username")
    role: str = payload.get("role")
    department: str = payload.get("department")
    employee_id: str = payload.get("employee_id")

    if not username or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "username": username,
        "role": role,
        "department": department,
        "employee_id": employee_id,
    }
