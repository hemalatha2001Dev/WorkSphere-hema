from fastapi import APIRouter, HTTPException, status, Depends, Request, Form
from fastapi.responses import JSONResponse
from datetime import timedelta, datetime, timezone
from pydantic import BaseModel, EmailStr, Field
import random, string, time
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError, ExpiredSignatureError
from fastapi.encoders import jsonable_encoder
from app.models.mysql_models import User
from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.utils.outlook_mail import send_otp_email
from app.utils.send_email import send_user_credentials_email
from app.schemas.user_schema import RegisterSchema, LoginSchema
from app.core.security import hash_password, verify_password, create_access_token
from app.crud.user_crud import create_user_mysql, create_user_mongo
from app.crud.dual_crud import get_user_by_identifier, update_user_password
from app.db.mongo import get_mongo_client
from app.db.mysql import get_mysql_session

router = APIRouter(prefix="/api/v1", tags=["Authentication"])

# In-memory stores
otp_store = {}
reset_token_store = {}

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")


# ------------------ Helper: Check User Status ------------------
def check_user_status(user_dict):
    """
    Verify user status allows login.
    Valid statuses: 'active', 'pending'
    Invalid statuses: 'inactive', 'suspended', 'deactivated'
    """
    user_status = user_dict.get("status", "active").lower()
    
    if user_status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is inactive. Please contact admin for login access."
        )
    
    if user_status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact admin."
        )
    
    if user_status == "deactivated":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Please contact admin."
        )
    
    if user_status not in ["active", "pending"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account status does not allow login. Please contact admin."
        )


# ------------------ Dependency: Get Current User ------------------
def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    if not token:
        token = request.cookies.get("access_token")  
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired, please log in again")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token, please provide a valid token")

    username: str = payload.get("username")
    role: str = payload.get("role")
    department: str = payload.get("department")
    employee_id: str = payload.get("employee_id")
    exp = payload.get("exp")

    if not username or not role:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=401, detail="Token has expired, please log in again")

    return {
        "username": username,
        "role": role,
        "department": department,
        "employee_id": employee_id
    }


# ------------------ REGISTER ------------------
@router.post("/register", tags=["Authentication"], status_code=status.HTTP_201_CREATED)
def register_user(user: RegisterSchema):
    """Register a new SuperAdmin user with designated employee ID"""
    
    # ✅ Force SuperAdmin role
    user_dict = user.dict()
    user_dict["role"] = "SuperAdmin"  # Override whatever was sent
    user_dict["status"] = "active"  # Set status to active by default

    # ✅ Get employee_id from request (required field)
    employee_id = user_dict.get("employee_id")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Employee ID is required")
    
    # ✅ Validate employee_id format (optional - customize as needed)
    if not isinstance(employee_id, str) or len(employee_id.strip()) == 0:
        raise HTTPException(status_code=400, detail="Employee ID must be a non-empty string")

    # Generate random password
    plain_password = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    
    # ✅ Check uniqueness in MongoDB
    mongo_client = get_mongo_client()
    if mongo_client:
        mongo_db = mongo_client[settings.MONGO_DB_NAME]
        existing_user = mongo_db["users"].find_one({"employee_id": employee_id})
        if existing_user:
            raise HTTPException(status_code=400, detail=f"Employee ID '{employee_id}' already exists in MongoDB")
    
    # ✅ Check uniqueness in MySQL
    db = next(get_mysql_session())
    try:
        existing_user_mysql = db.query(User).filter(User.employee_id == employee_id).first()
        if existing_user_mysql:
            raise HTTPException(status_code=400, detail=f"Employee ID '{employee_id}' already exists in MySQL")
    finally:
        db.close()

    # ✅ Hash password
    user_dict["password"] = hash_password(plain_password)


    try:
        # MongoDB PRIMARY creation
        mongo_id = create_user_mongo(user_dict)
        if not mongo_id:
            raise HTTPException(status_code=500, detail="Failed to create user in primary database")

        # ✅ Filter MySQL-compatible fields
        mysql_allowed_fields = User.__table__.columns.keys()
        mysql_user_dict = {k: v for k, v in user_dict.items() if k in mysql_allowed_fields}

        # ✅ MySQL fallback
        create_user_mysql(mysql_user_dict)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected database error: {str(e)}")

    # ✅ Send credentials email
    try:
        send_user_credentials_email(user.email, user.username, plain_password)
    except Exception as e:
        print(f"Failed to send email: {e}")

    # ✅ Create JWT token for SuperAdmin
    token = create_access_token(
        {
            "username": user.username,
            "role": "SuperAdmin",
            "department": user.department,
            "employee_id": employee_id,
            "status": "active"
        },
        expires_delta=timedelta(days=1),
    )

    return {
        "message": "SuperAdmin user created successfully",
        "access_token": token,
        "employee_id": employee_id,
        "username": user.username,
        "role": "SuperAdmin"
    }


# ------------------ LOGIN ------------------
@router.post("/login", tags=["Authentication"])
async def login(
    request: Request,
    username: str = Form(None),
    password: str = Form(None),
):
    """
    Unified login endpoint for both frontend (JSON) and Swagger (form-data).
    Checks user status - inactive users cannot login.
    
    Frontend usage (JSON):
    POST /api/v1/login
    Content-Type: application/json
    {
        "username": "user123",
        "password": "pass123"
    }
    
    Swagger usage (form-data):
    POST /api/v1/login
    username: user123
    password: pass123
    
    Status Check:
    - active: Can login
    - pending: Can login
    - inactive: Cannot login - "Please contact admin for login access"
    - suspended: Cannot login - "Your account has been suspended"
    - deactivated: Cannot login - "Your account has been deactivated"
    """
    try:
        identifier = None
        pwd = None
        
        # Try to get credentials from JSON (frontend)
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = await request.json()
                identifier = payload.get("username") or payload.get("email")
                pwd = payload.get("password")
            except Exception:
                pass
        
        # If JSON parsing failed or no data, try form data (Swagger)
        if not identifier or not pwd:
            if username and password:
                identifier = username
                pwd = password
        
        # Final validation and cleanup
        if identifier:
            identifier = str(identifier).strip()
        if pwd:
            pwd = str(pwd).strip()

        if not identifier or not pwd:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username/email and password are required"
            )

        # Get user from database
        user = get_user_by_identifier(identifier)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Convert SQLAlchemy object to dict
        if hasattr(user, "__dict__"):
            user_dict = {k: v for k, v in user.__dict__.items() if not k.startswith("_")}
        else:
            user_dict = user

        # Convert MongoDB ObjectId to string
        if "_id" in user_dict:
            user_dict["_id"] = str(user_dict["_id"])

        # ✅ CHECK USER STATUS - BEFORE PASSWORD VERIFICATION
        check_user_status(user_dict)

        # Verify password
        stored_password = user_dict.get("password")
        if not stored_password or not verify_password(pwd, stored_password):
            # Log the attempt for security
            print(f"Login failed for user: {identifier}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password"
            )

        # Create JWT token
        token = create_access_token(
            {
                "username": user_dict["username"],
                "role": user_dict.get("role"),
                "department": user_dict.get("department"),
                "employee_id": user_dict.get("employee_id"),
                "status": user_dict.get("status", "active"),
            },
            expires_delta=timedelta(days=1),
        )

        # Remove sensitive data
        user_dict.pop("password", None)
        user_dict.pop("_id", None)

        # Convert datetime objects to ISO format strings
        def serialize_datetime(obj):
            """Convert datetime objects to ISO format strings"""
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: serialize_datetime(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [serialize_datetime(item) for item in obj]
            return obj

        # Serialize all datetime fields in user_dict
        user_dict = serialize_datetime(user_dict)

        # Create response
        response = JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "Logged in successfully",
                "access_token": token,
                "token_type": "bearer",
                "user": user_dict,
            }
        )

        # Set secure cookie
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,
            samesite="Lax",
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        print(f"Login Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login error: {str(e)}"
        )


# ------------------ FORGOT PASSWORD ------------------
class ForgotPasswordRequest(BaseModel):
    email: EmailStr

@router.post("/forgot-password", tags=["Authentication"])
def forgot_password(request: ForgotPasswordRequest):
    """Request OTP for password reset"""
    user = get_user_by_identifier(request.email)
    if not user:
        raise HTTPException(status_code=404, detail="Email not found")

    # ✅ Check if user is active
    if hasattr(user, "__dict__"):
        user_dict = {k: v for k, v in user.__dict__.items() if not k.startswith("_")}
    else:
        user_dict = user
    
    user_status = user_dict.get("status", "active").lower()
    if user_status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is inactive. Contact admin to reset password."
        )

    otp = str(random.randint(100000, 999999))
    expiry = time.time() + 600  # 10 minutes
    otp_store[request.email] = {"otp": otp, "expiry": expiry}

    try:
        send_otp_email(request.email, otp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send OTP: {e}")

    return {"message": "OTP sent to your email. Valid for 10 minutes."}


# ------------------ VERIFY OTP ------------------
class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)

@router.post("/verify-otp", tags=["Authentication"])
def verify_otp(request: VerifyOtpRequest):
    """Verify OTP and get reset token"""
    record = otp_store.get(request.email)
    if not record or time.time() > record["expiry"]:
        raise HTTPException(status_code=400, detail="OTP expired or not found")

    if request.otp != record["otp"]:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    reset_token_store[request.email] = {"reset_token": token, "expiry": time.time() + 900}  # 15 minutes

    del otp_store[request.email]

    return {"message": "OTP verified successfully", "reset_token": token}


# ------------------ RESET PASSWORD ------------------
class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)

@router.post("/reset-password", tags=["Authentication"])
def reset_password(request: ResetPasswordRequest):
    """Reset password using reset token"""
    if request.new_password != request.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Find user email by reset token
    user_email = None
    for email, record in reset_token_store.items():
        if record.get("reset_token") == request.reset_token:
            user_email = email
            break
    
    if not user_email:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    record = reset_token_store[user_email]
    if time.time() > record["expiry"]:
        # Clean up expired token
        del reset_token_store[user_email]
        raise HTTPException(status_code=400, detail="Reset token has expired. Request a new one.")

    # Update password in both databases
    hashed_pw = hash_password(request.new_password)
    updated = update_user_password(user_email, hashed_pw)

    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update password")

    # Clean up used token
    del reset_token_store[user_email]

    return {"message": "Password reset successfully. You can now login with your new password."}


