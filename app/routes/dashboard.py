# app/routers/dashboard.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from sqlalchemy import func
from app.db.mysql import get_mysql_session
from app.models.mysql_models import User
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/employee/dashboard", tags=["Employee Dashboard"])

@router.get("/")
def get_dashboard(
    start_date: date = Query(None),
    end_date: date = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    # Base query with role-based filtering
    base_query = db.query(User)
    
    # Role-based access control
    if current_user["role"] == "SuperAdmin":
        # SuperAdmin sees all employees
        pass
    elif current_user["role"] == "Admin":
        # Admin sees only their department
        department = current_user["department"]
        base_query = base_query.filter(User.department == department)
    elif current_user["role"] == "Employee":
        # Employee sees only their own data
        employee_username = current_user["username"]
        base_query = base_query.filter(User.username == employee_username)
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role")
    
    # Optional date filter
    if start_date and end_date:
        base_query = base_query.filter(User.joining_date.between(start_date, end_date))

    # --- Key Metrics ---
    total_employees = base_query.count()
    
    # Department count based on role
    if current_user["role"] == "SuperAdmin":
        departments_count = db.query(func.count(func.distinct(User.department))).scalar()
    elif current_user["role"] == "Admin":
        # Admin only sees their department (count = 1)
        departments_count = 1
    else:
        # Employee doesn't need department count
        departments_count = 0
    
    # Active count within the filtered scope
    active_query = base_query.filter(User.status == "Active")
    active_count = active_query.count()

    # --- Department-wise distribution ---
    dept_query = base_query.with_entities(User.department, func.count(User.id)).group_by(User.department)
    dept_distribution = dept_query.all()
    dept_data = {dept: count for dept, count in dept_distribution}

    # --- Employee status distribution ---
    active_status = base_query.filter(User.status == "Active").count()
    inactive_status = base_query.filter(User.status == "Inactive").count()

    return {
        "key_metrics": {
            "total_employees": total_employees,
            "departments": departments_count,
            "active": active_count
        },
        "department_distribution": dept_data,
        "employee_status": {
            "active": active_status,
            "inactive": inactive_status
        },
        "role": current_user["role"]
    }