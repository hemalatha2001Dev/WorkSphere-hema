import random
import string
from datetime import timedelta
from fastapi import APIRouter, HTTPException, status, Depends, Query, Request
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.models.mysql_models import User
from app.schemas.user_schema import EmployeeRegisterSchema, EmployeeUpdateSchema
from app.core.security import hash_password, create_access_token
from app.crud.user_crud import create_user_mysql, create_user_mongo
from app.utils.send_email import send_user_credentials_email
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1", tags=["Employee"])


# ------------------ Helper: Normalize custom_fields ------------------
def normalize_custom_fields(custom_fields):
    """
    Ensure custom_fields is always a list of {name, value} dicts.
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


# ------------------ Helper: Check uniqueness ------------------
def check_unique_fields(employee_id, username, email, phone, mongo_client, db, exclude_employee_id=None):
    """
    Check if username, email, phone, and employee_id are unique across both databases.
    exclude_employee_id: When updating, exclude the current employee from uniqueness check
    """
    # Check MongoDB
    if mongo_client:
        mongo_db = mongo_client["manger_db"]
        
        # Check employee_id
        if employee_id:
            query = {"employee_id": employee_id}
            if exclude_employee_id:
                query["employee_id"] = {"$eq": employee_id, "$ne": exclude_employee_id}
            if mongo_db["users"].find_one(query):
                raise HTTPException(status_code=400, detail=f"Employee ID '{employee_id}' already exists")
        
        # Check username - THIS IS THE CRITICAL FIX
        if username:
            query = {"username": username}
            if exclude_employee_id:
                query["employee_id"] = {"$ne": exclude_employee_id}
            if mongo_db["users"].find_one(query):
                raise HTTPException(status_code=400, detail=f"Username '{username}' already exists")
        
        # Check email
        if email:
            query = {"email": email}
            if exclude_employee_id:
                query["employee_id"] = {"$ne": exclude_employee_id}
            if mongo_db["users"].find_one(query):
                raise HTTPException(status_code=400, detail=f"Email '{email}' already exists")
        
        # Check phone
        if phone:
            query = {"phone": phone}
            if exclude_employee_id:
                query["employee_id"] = {"$ne": exclude_employee_id}
            if mongo_db["users"].find_one(query):
                raise HTTPException(status_code=400, detail=f"Phone number '{phone}' already exists")
    
    # Check MySQL
    if employee_id:
        query = db.query(User).filter(User.employee_id == employee_id)
        if exclude_employee_id:
            query = query.filter(User.employee_id != exclude_employee_id)
        if query.first():
            raise HTTPException(status_code=400, detail=f"Employee ID '{employee_id}' already exists")
    
    if username:
        query = db.query(User).filter(User.username == username)
        if exclude_employee_id:
            query = query.filter(User.employee_id != exclude_employee_id)
        if query.first():
            raise HTTPException(status_code=400, detail=f"Username '{username}' already exists")
    
    if email:
        query = db.query(User).filter(User.email == email)
        if exclude_employee_id:
            query = query.filter(User.employee_id != exclude_employee_id)
        if query.first():
            raise HTTPException(status_code=400, detail=f"Email '{email}' already exists")
    
    if phone:
        query = db.query(User).filter(User.phone == phone)
        if exclude_employee_id:
            query = query.filter(User.employee_id != exclude_employee_id)
        if query.first():
            raise HTTPException(status_code=400, detail=f"Phone number '{phone}' already exists")


# ------------------ REGISTER EMPLOYEE ------------------
@router.post("/employee-register", status_code=status.HTTP_201_CREATED)
def employee_register(
    employee: EmployeeRegisterSchema,
    current_user: dict = Depends(get_current_user)
):
    # Admin cannot create another Admin - Only SuperAdmin can create Admin
    if current_user["role"] == "Admin":
        if employee.role in ["Admin", "SuperAdmin"]:
            raise HTTPException(status_code=403, detail="Admin can only create Employee users")
    
    # Only SuperAdmin can create SuperAdmin
    if current_user["role"] != "SuperAdmin" and employee.role == "SuperAdmin":
        raise HTTPException(status_code=403, detail="Only SuperAdmin can create SuperAdmin users")
    
    plain_password = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    employee_dict = employee.dict()

    employee_id = employee_dict.get("employee_id")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Employee ID is required")
    
    # Check uniqueness for all fields
    mongo_client = get_mongo_client()
    db = next(get_mysql_session())
    try:
        check_unique_fields(
            employee_id=employee_id,
            username=employee_dict.get("username"),
            email=employee_dict.get("email"),
            phone=employee_dict.get("phone"),
            mongo_client=mongo_client,
            db=db
        )
    finally:
        db.close()

    employee_dict["custom_fields"] = normalize_custom_fields(employee_dict.get("custom_fields"))
    employee_dict["password"] = hash_password(plain_password)

    # Remove file fields if present
    employee_dict.pop("file_mongo_id", None)
    employee_dict.pop("file_name", None)
    employee_dict.pop("file_content_type", None)

    try:
        # MongoDB PRIMARY (use a copy)
        mongo_payload = dict(employee_dict)
        mongo_created = create_user_mongo(mongo_payload)
        if not mongo_created:
            raise HTTPException(status_code=500, detail="Failed to create employee in primary database")

        # MySQL FALLBACK (clean copy, ensure no Mongo keys like _id)
        mysql_payload = dict(employee_dict)
        mysql_payload.pop("_id", None)
        create_user_mysql(mysql_payload)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected database error: {e}")

    # Send credentials email
    try:
        send_user_credentials_email(
            to_email=employee.email,
            username=employee.username,
            password=plain_password
        )
    except Exception as e:
        print(f"Failed to send email: {e}")

    # Generate access token
    token = create_access_token(
        {
            "username": employee.username,
            "role": employee.role,
            "department": employee.department
        },
        expires_delta=timedelta(days=1)
    )

    return {
        "message": "Employee registered successfully", 
        "access_token": token,
        "employee_id": employee_id
    }


# ------------------ GET ALL EMPLOYEES ------------------
@router.get("/employees", status_code=200)
def get_all_employees(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(None),
    department: str = Query(None),
    mongo_client=Depends(get_mongo_client),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    # Role-based access
    if current_user["role"] == "SuperAdmin":
        pass
    elif current_user["role"] == "Admin":
        department = current_user["department"]
    elif current_user["role"] == "Employee":
        search = None
        department = None
        employee_username = current_user["username"]
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role")

    page_size = 10
    skip = (page - 1) * page_size

    # MongoDB logic
    try:
        if mongo_client:
            mongo_db = mongo_client["manger_db"]
            query = {}
            filters = []

            if department:
                filters.append({"department": {"$regex": department, "$options": "i"}})
            if search:
                filters.append({
                    "$or": [
                        {"firstname": {"$regex": search, "$options": "i"}},
                        {"lastname": {"$regex": search, "$options": "i"}},
                        {"email": {"$regex": search, "$options": "i"}},
                        {"phone": {"$regex": search, "$options": "i"}}
                    ]
                })
            if current_user["role"] == "Employee":
                filters.append({"username": employee_username})

            if filters:
                query = {"$and": filters}

            cursor = mongo_db["users"].find(query).skip(skip).limit(page_size)
            employees = list(cursor)
            total_employees = mongo_db["users"].count_documents(query)

            for e in employees:
                e["_id"] = str(e["_id"])
                e.pop("password", None)  # Remove password
                e["custom_fields"] = normalize_custom_fields(e.get("custom_fields"))

                # Handle profile_image URL construction
                if e.get("profile_image"):
                    if not e["profile_image"].startswith(('http://', 'https://')):
                        e["profile_image"] = str(request.base_url) + f"uploads/employee/{e['profile_image']}"
                elif e.get("file_name") and e.get("category"):
                    e["profile_image"] = str(request.base_url) + f"uploads/{e['category']}/{e['file_name']}"
                    e["file_url"] = e["profile_image"]

            if employees:
                return {
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_employees + page_size - 1) // page_size,
                    "total_employees": total_employees,
                    "data": jsonable_encoder(employees)
                }

    except Exception as e:
        print(f"Mongo fetch failed: {e}")

    # Fallback: MySQL
    sql_query = db.query(User)
    if department:
        sql_query = sql_query.filter(User.department.ilike(f"%{department}%"))
    if search:
        pattern = f"%{search}%"
        sql_query = sql_query.filter(
            (User.firstname.ilike(pattern)) |
            (User.lastname.ilike(pattern)) |
            (User.email.ilike(pattern)) |
            (User.phone.ilike(pattern))
        )
    if current_user["role"] == "Employee":
        sql_query = sql_query.filter(User.username == employee_username)

    total_employees = sql_query.count()
    employees = sql_query.offset(skip).limit(page_size).all()

    employees_list = []
    for emp in employees:
        emp_dict = emp.__dict__.copy()
        emp_dict.pop("password", None)  # Remove password
        emp_dict["custom_fields"] = normalize_custom_fields(emp_dict.get("custom_fields"))
        
        if emp_dict.get("profile_image"):
            if not emp_dict["profile_image"].startswith(('http://', 'https://')):
                emp_dict["profile_image"] = str(request.base_url) + f"uploads/employee/{emp_dict['profile_image']}"
        
        employees_list.append(emp_dict)

    return {
        "page": page,
        "page_size": page_size,
        "total_pages": (total_employees + page_size - 1) // page_size,
        "total_employees": total_employees,
        "data": employees_list
    }

# ------------------ UPDATE EMPLOYEE (FIXED) ------------------
@router.put("/employee", status_code=status.HTTP_200_OK)
def update_employee(
    employee: EmployeeUpdateSchema, 
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    emp_id = employee.employee_id
    employee_data = employee.dict(exclude_unset=True)
    
    # 1. Self-Update: Allow any user (Employee, Admin, SuperAdmin) to update their own profile
    if current_user.get("employee_id") == emp_id:
        # Self-update: Remove sensitive fields to prevent unauthorized elevation/change
        if current_user["role"] != "SuperAdmin":
            employee_data.pop("role", None)
            employee_data.pop("status", None)
            # You might need to fetch the existing role/status for the response, 
            # but for the update itself, removing the fields is sufficient.
    
    # 2. Permission Check for Updating OTHERS
    elif current_user["role"] == "Admin":
        # Admin updating another user (not self)
        db_employee = db.query(User).filter(User.employee_id == emp_id).first()
        if not db_employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Admin CANNOT update other Admin or SuperAdmin
        if db_employee.role in ["Admin", "SuperAdmin"]:
            raise HTTPException(status_code=403, detail="You do not have permission to update Admin or SuperAdmin users. Contact your SuperAdmin.")
        
        # Also prevent Admin from changing role to Admin/SuperAdmin
        if "role" in employee_data and employee_data["role"] in ["Admin", "SuperAdmin"]:
            raise HTTPException(status_code=403, detail="You can only update Employee role. Contact your SuperAdmin to create Admin users.")

    # 3. Final Permission Check (For users updating others)
    elif current_user["role"] != "SuperAdmin":
        # Catches employees trying to update others, or any other unauthorized role
        raise HTTPException(status_code=403, detail="Insufficient permissions to update employee")

    # SuperAdmin logic: Allow SuperAdmin to update anyone, including role change
    if current_user["role"] == "SuperAdmin":
        if "role" in employee_data and employee_data["role"] not in ["SuperAdmin", "Admin", "Employee"]:
            raise HTTPException(status_code=400, detail="Invalid role")
    
    # Check uniqueness for username, email and phone if they're being updated
    mongo_client = get_mongo_client()
    if "username" in employee_data or "email" in employee_data or "phone" in employee_data:
        try:
            check_unique_fields(
                employee_id=None,  # Don't check employee_id during update
                username=employee_data.get("username"),
                email=employee_data.get("email"),
                phone=employee_data.get("phone"),
                mongo_client=mongo_client,
                db=db,
                exclude_employee_id=emp_id  # Exclude current employee
            )
        except HTTPException as e:
            print(f"Uniqueness check failed: {e}")
            raise

    # Normalize custom_fields if provided
    if "custom_fields" in employee_data:
        employee_data["custom_fields"] = normalize_custom_fields(employee_data.get("custom_fields"))

    # Update MySQL first (primary source of truth)
    mysql_updated = False
    try:
        db_employee = db.query(User).filter(User.employee_id == emp_id).first()
        if not db_employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Update only the fields that are provided and not None
        updated_fields = {}
        for key, value in employee_data.items():
            if value is not None and hasattr(db_employee, key):
                setattr(db_employee, key, value)
                updated_fields[key] = value
        
        if updated_fields:
            db.commit()
            db.refresh(db_employee)
            mysql_updated = True
        else:
            # No fields to update
            mysql_updated = True
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"MySQL update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")
    
    # Update MongoDB to keep in sync
    try:
        client = get_mongo_client()
        if client:
            db_mongo = client["manger_db"]
            
            # Only update fields that are provided and not None
            update_payload = {k: v for k, v in employee_data.items() if v is not None}
            
            if update_payload:
                result = db_mongo["users"].update_one(
                    {"employee_id": emp_id},
                    {"$set": update_payload}
                )
                if result.matched_count == 0:
                    print(f"MongoDB: No document found with employee_id {emp_id}")
    except Exception as e:
        print(f"MongoDB update failed (non-critical): {e}")
    
    if not mysql_updated:
        # This should theoretically not be hit if the employee was found earlier
        raise HTTPException(status_code=404, detail="Employee not found during final check")

    return {"message": "Employee updated successfully", "employee_id": emp_id}


# ------------------ UPDATE PROFILE IMAGE ------------------
@router.put("/employee/{employee_id}/profile-image", status_code=status.HTTP_200_OK)
def update_profile_image(
    employee_id: str,
    profile_image: str = Query(..., description="URL of the profile image"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Update only the profile image for an employee
    """
    # Update in MySQL first
    db_employee = db.query(User).filter(User.employee_id == employee_id).first()
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    # Role-based check for the profile image update
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Employees can only update their own profile image.")
    elif current_user["role"] == "Admin" and db_employee.role in ["Admin", "SuperAdmin"] and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Admin cannot update profile image for other Admin or SuperAdmin users.")
    elif current_user["role"] not in ["SuperAdmin", "Admin", "Employee"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    
    db_employee.profile_image = profile_image
    db.commit()
    db.refresh(db_employee)

    # Update in MongoDB to keep in sync
    try:
        client = get_mongo_client()
        if client:
            db_mongo = client["manger_db"]
            db_mongo["users"].update_one(
                {"employee_id": employee_id},
                {"$set": {"profile_image": profile_image}}
            )
    except Exception as e:
        print(f"MongoDB profile image update failed (non-critical): {e}")

    return {"message": "Profile image updated successfully", "employee_id": employee_id}


# ------------------ GET EMPLOYEE BY ID ------------------
@router.get("/employee/{employee_id}", status_code=200)
def get_employee_by_id(
    employee_id: str,
    request: Request,
    mongo_client=Depends(get_mongo_client),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific employee by ID
    """
    # Role-based access control
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Try MongoDB first
    try:
        if mongo_client:
            mongo_db = mongo_client["manger_db"]
            employee = mongo_db["users"].find_one({"employee_id": employee_id})
            
            if employee:
                employee["_id"] = str(employee["_id"])
                employee.pop("password", None)  # Remove password
                employee["custom_fields"] = normalize_custom_fields(employee.get("custom_fields"))

                if employee.get("profile_image"):
                    if not employee["profile_image"].startswith(('http://', 'https://')):
                        employee["profile_image"] = str(request.base_url) + f"uploads/employee/{employee['profile_image']}"
                elif employee.get("file_name") and employee.get("category"):
                    employee["profile_image"] = str(request.base_url) + f"uploads/{employee['category']}/{employee['file_name']}"

                return jsonable_encoder(employee)
    except Exception as e:
        print(f"MongoDB fetch failed: {e}")

    # Fallback to MySQL
    db_employee = db.query(User).filter(User.employee_id == employee_id).first()
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    employee_dict = db_employee.__dict__.copy()
    employee_dict.pop("password", None)  # Remove password
    employee_dict["custom_fields"] = normalize_custom_fields(employee_dict.get("custom_fields"))
    
    if employee_dict.get("profile_image"):
        if not employee_dict["profile_image"].startswith(('http://', 'https://')):
            employee_dict["profile_image"] = str(request.base_url) + f"uploads/employee/{employee_dict['profile_image']}"

    return employee_dict

# ------------------ DELETE EMPLOYEE ------------------
@router.delete("/employee/{employee_id}", status_code=status.HTTP_200_OK)
def delete_employee(
    employee_id: str, 
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    # Role-based access control
    if current_user["role"] not in ["SuperAdmin", "Admin"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Admin cannot delete Admin or SuperAdmin (even if it's themselves)
    db_employee = db.query(User).filter(User.employee_id == employee_id).first()
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    if current_user["role"] == "Admin" and db_employee.role in ["Admin", "SuperAdmin"]:
        # Only SuperAdmin can delete Admin/SuperAdmin accounts
        raise HTTPException(status_code=403, detail="Admin cannot delete Admin or SuperAdmin users")

    # Prevent a user from deleting themselves (requires SuperAdmin intervention)
    if current_user.get("employee_id") == employee_id:
        if current_user["role"] != "SuperAdmin":
            raise HTTPException(status_code=403, detail="You cannot delete your own active account. Please contact a SuperAdmin.")


    db.delete(db_employee)
    db.commit()

    try:
        client = get_mongo_client()
        if client:
            mongo_db = client["manger_db"]
            mongo_db["users"].delete_one({"employee_id": employee_id})
    except Exception as e:
        print(f"MongoDB delete error: {e}")

    return {"message": "Employee deleted successfully", "employee_id": employee_id}