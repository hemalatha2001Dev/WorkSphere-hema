from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Request, Depends ,Form
from pymongo import MongoClient
import os
from datetime import datetime , timedelta
from uuid import uuid4
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.routes.project import Project,apply_role_based_filter
from typing import List, Optional
from pydantic import BaseModel
from app.models.mysql_models import User, Project, Task
from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.core.security import get_current_user,hash_password
from app.utils.send_email import send_user_credentials_email
from app.core.config import settings
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, ValidationError, field_validator,validator
from typing import Optional, Union, List, Literal, Dict, Any
import pandas as pd
import io
import re
import random
import string
import json


router = APIRouter(prefix="/api/v1/common", tags=["Common"])

# MongoDB setup
mongo_client = get_mongo_client()
db = mongo_client[settings.MONGO_DB_NAME]
files_collection = db["uploaded_files"]

BASE_UPLOAD_DIR = "uploads"
os.makedirs(BASE_UPLOAD_DIR, exist_ok=True)





@router.post("/upload-file")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    category: str = Query(..., description="Category like 'employee', 'project', etc.")
):
    """
    Upload a file into the uploads/<category> folder and store metadata in MongoDB.
    Returns the public file URL.
    """
    category = category.strip().lower()

    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")

    from app.utils.gcs import upload_content_to_gcs
    try:
        content = await file.read()
        file_url = upload_content_to_gcs(
            content=content, 
            original_filename=file.filename,
            content_type=file.content_type,
            category=category
        )
        if not file_url:
            raise HTTPException(status_code=500, detail="Failed to upload file to GCS")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Prepare MongoDB record
    file_data = {
        "file_name": file.filename, 
        "original_name": file.filename,
        "file_content_type": file.content_type,
        "file_path": file_url,
        "category": category,
        "uploaded_at": datetime.utcnow()
    }

    result = files_collection.insert_one(file_data)

    # Get full host URL
    base_url = str(request.base_url).rstrip("/")
    file_url = f"{base_url}/uploads/{category}/{filename}"

    return {
        "file_mongo_id": str(result.inserted_id),
        "file_name": file.filename,
        "original_file_name": file.filename,
        "file_content_type": file.content_type,
        "category": category,
        "file_url": file_url
    }
@router.put("/update-profile", response_model=dict)
async def update_profile(
    request: Request,
    employee_id: str = Form(...),
    firstname: str = Form(None),
    lastname: str = Form(None),
    phone: str = Form(None),
    current_address: str = Form(None),
    permanent_address: str = Form(None),
    custom_fields: Optional[str] = Form(None),
    profile_image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Update user profile including profile image.
    FIXED: Properly handles profile image replacement
    """
    try:
        import json
        
        # Validate employee_id
        if not employee_id:
            raise HTTPException(status_code=400, detail="employee_id is required")
        
        # Get user by employee_id
        user = db.query(User).filter(User.employee_id == employee_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Permission check - users can only update their own profile unless admin/superadmin
        if current_user["role"] == "Employee":
            if current_user.get("employee_id") != employee_id:
                raise HTTPException(status_code=403, detail="You can only update your own profile")
        elif current_user["role"] == "Admin":
            # Admin can update users in their department
            if user.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="You can only update users in your department")
        
        # Prepare update data
        update_data = {}
        
        if firstname:
            update_data["firstname"] = firstname
        if lastname:
            update_data["lastname"] = lastname
        if phone:
            update_data["phone"] = phone
        if current_address:
            update_data["current_address"] = current_address
        if permanent_address:
            update_data["permanent_address"] = permanent_address
        
        # Handle custom fields
        if custom_fields:
            try:
                custom_fields_list = json.loads(custom_fields)
                update_data["custom_fields"] = custom_fields_list
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid custom_fields JSON format")
        
        # Handle profile image upload - FIXED VERSION
        if profile_image:
            # Delete old profile image if exists
            if user.profile_image:
                try:
                    # Extract the file path from the URL
                    old_image_url = user.profile_image
                    
                    # Handle both full URLs and relative paths
                    if old_image_url.startswith('http'):
                        # Extract path after /uploads/
                        match = re.search(r'/uploads/(.+)$', old_image_url)
                        if match:
                            relative_path = match.group(1)
                            old_file_path = os.path.join(BASE_UPLOAD_DIR, relative_path)
                        else:
                            old_file_path = None
                    else:
                        # It's already a relative path
                        old_file_path = old_image_url if old_image_url.startswith('uploads/') else os.path.join(BASE_UPLOAD_DIR, old_image_url)
                    
                    # Delete the old file if it exists
                    if old_file_path and os.path.exists(old_file_path):
                        os.remove(old_file_path)
                        print(f"Deleted old profile image: {old_file_path}")
                except Exception as e:
                    print(f"Error deleting old profile image: {e}")
                    # Continue anyway - don't fail the update if delete fails
            
            # Save new profile image
            from app.utils.gcs import upload_content_to_gcs
            category = "profile"
            
            # Validate file type
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
            ext = Path(profile_image.filename).suffix.lower()
            
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid file type. Allowed types: {', '.join(allowed_extensions)}"
                )
            
            try:
                content = await profile_image.read()
                
                # Validate file size (e.g., max 5MB)
                max_size = 5 * 1024 * 1024  # 5MB
                if len(content) > max_size:
                    raise HTTPException(status_code=400, detail="File size too large. Maximum 5MB allowed.")
                
                # Upload to GCS
                profile_image_url = upload_content_to_gcs(
                    content=content,
                    original_filename=profile_image.filename,
                    content_type=profile_image.content_type,
                    category=category
                )
                
                if not profile_image_url:
                    raise HTTPException(status_code=500, detail="Failed to save profile image to GCS")
                
                print(f"Saved new profile image: {profile_image_url}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to save profile image: {str(e)}")
            
            update_data["profile_image"] = profile_image_url
        
        # Update MySQL
        for key, value in update_data.items():
            setattr(user, key, value)
        
        if hasattr(user, 'updated_at'):
            user.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(user)
        
        # Update MongoDB
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_update = {**update_data}
            mongo_update["updated_at"] = datetime.utcnow()
            
            result = mongo_db["users"].update_one(
                {"employee_id": employee_id},
                {"$set": mongo_update}
            )
            
            if result.matched_count > 0:
                print(f"Updated MongoDB for employee_id: {employee_id}")
        
        return {
            "message": "Profile updated successfully",
            "employee_id": user.employee_id,
            "username": user.username,
            "firstname": user.firstname,
            "lastname": user.lastname,
            "email": user.email,
            "phone": user.phone,
            "current_address": user.current_address,
            "permanent_address": user.permanent_address,
            "profile_image": user.profile_image,
            "custom_fields": user.custom_fields,
            "department": user.department,
            "role": user.role
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating profile: {str(e)}")


@router.get("/metrics/{employee_id}", response_model=dict)
def get_employee_metrics(
    employee_id: str,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Get employee metrics:
    1. Projects count by status (Not Started, To Do, In Progress, Completed)
    2. Tasks count by status (Pending, In Progress, Completed, Review)
    3. Total completed tasks
    """
    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            # Get projects where employee is admin or team member
            projects_query = {
                "is_deleted": {"$ne": True},
                "$or": [
                    {"project_admins": {"$in": [employee_id]}},
                    {"selected_team_members": {"$in": [employee_id]}}
                ]
            }
            
            projects = list(mongo_db["projects"].find(projects_query, {"_id": 0}))
            
            # Count projects by status
            project_metrics = {
                "Not_Started": sum(1 for p in projects if p.get("project_status") == "Not Started"),
                "To_Do": sum(1 for p in projects if p.get("project_status") == "To Do"),
                "In_Progress": sum(1 for p in projects if p.get("project_status") == "In Progress"),
                "Completed": sum(1 for p in projects if p.get("project_status") == "Completed")
            }
            project_metrics["total_projects"] = len(projects)
            
            # Get tasks assigned to or involved with employee
            tasks_query = {
                "is_deleted": {"$ne": True},
                "$or": [
                    {"assignee": employee_id},
                    {"selected_team_members": {"$in": [employee_id]}}
                ]
            }
            
            tasks = list(mongo_db["tasks"].find(tasks_query, {"_id": 0}))
            
            # Count tasks by status
            task_metrics = {
                "Pending": sum(1 for t in tasks if t.get("status") == "Pending"),
                "In_Progress": sum(1 for t in tasks if t.get("status") == "In Progress"),
                "Review": sum(1 for t in tasks if t.get("status") == "Review"),
                "Completed": sum(1 for t in tasks if t.get("status") == "Completed")
            }
            task_metrics["total_tasks"] = len(tasks)
            
            # Total completed tasks
            total_completed_tasks = task_metrics["Completed"]
            
            return {
                "source": "mongo",
                "employee_id": employee_id,
                "projects": project_metrics,
                "tasks": task_metrics,
                "total_completed_tasks": total_completed_tasks
            }
        
        # Fallback to MySQL
        from app.models.mysql_models import Project, Task
        
        # Get projects where employee is admin or team member
        projects = db.query(Project).filter(
            and_(
                Project.is_deleted == False,
                or_(
                    Project.project_admins.contains([employee_id]),
                    Project.selected_team_members.contains([employee_id])
                )
            )
        ).all()
        
        # Count projects by status
        project_metrics = {
            "Not_Started": sum(1 for p in projects if p.project_status == "Not Started"),
            "To_Do": sum(1 for p in projects if p.project_status == "To Do"),
            "In_Progress": sum(1 for p in projects if p.project_status == "In Progress"),
            "Completed": sum(1 for p in projects if p.project_status == "Completed")
        }
        project_metrics["total_projects"] = len(projects)
        
        # Get tasks assigned to or involved with employee
        tasks = db.query(Task).filter(
            and_(
                Task.is_deleted == False,
                or_(
                    Task.assignee == employee_id,
                    Task.selected_team_members.contains([employee_id])
                )
            )
        ).all()
        
        # Count tasks by status
        task_metrics = {
            "Pending": sum(1 for t in tasks if t.status == "Pending"),
            "In_Progress": sum(1 for t in tasks if t.status == "In Progress"),
            "Review": sum(1 for t in tasks if t.status == "Review"),
            "Completed": sum(1 for t in tasks if t.status == "Completed")
        }
        task_metrics["total_tasks"] = len(tasks)
        
        # Total completed tasks
        total_completed_tasks = task_metrics["Completed"]
        
        return {
            "source": "mysql",
            "employee_id": employee_id,
            "projects": project_metrics,
            "tasks": task_metrics,
            "total_completed_tasks": total_completed_tasks
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching employee metrics: {str(e)}")


# MANAGEMENT APIs - Reusable across different modules
@router.post("/departments", response_model=dict)
def create_department(
    department_data: dict,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Create new department"""
    try:
        # Check if user is Admin or SuperAdmin
        if current_user.get("role") not in ("Admin", "SuperAdmin"):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Predefined 9 departments
        departments = [
            {"id": 1, "name": "HR", "description": "Human resources and talent management"},
            {"id": 2, "name": "ADMIN", "description": "Administrative services and management"},
            {"id": 3, "name": "LEGAL", "description": "Legal affairs and compliance"},
            {"id": 4, "name": "TENDERS", "description": "Procurement and tender management"},
            {"id": 5, "name": "FINANCE", "description": "Financial planning and accounting"},
            {"id": 6, "name": "IT", "description": "Information technology and systems"},
            {"id": 7, "name": "OPERATIONS", "description": "Business operations and process management"},
            {"id": 8, "name": "STORES", "description": "Inventory and warehouse management"},
            {"id": 9, "name": "PURCHASE", "description": "Procurement and purchasing operations"}
        ]
        
        return {
            "message": "Departments created successfully",
            "departments": departments
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating departments: {str(e)}")
    



@router.get("/metrics/{employee_id}", response_model=dict)
def get_employee_metrics(
    employee_id: str,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Get employee metrics:
    1. Projects count by status (Not Started, ToDo, In Progress, Completed)
    2. Tasks count by status (Pending, In Progress, Completed)
    3. Total completed tasks
    """
    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            # Get projects where employee is admin or team member (check both username and employee_id)
            projects_query = {
                "is_deleted": {"$ne": True},
                "$or": [
                    {"project_admins": {"$in": [employee_id]}},
                    {"selected_team_members": {"$in": [employee_id]}},
                    {"project_admins": {"$in": [current_user.get("username")]}},
                    {"selected_team_members": {"$in": [current_user.get("username")]}}
                ]
            }
            
            projects = list(mongo_db["projects"].find(projects_query, {"_id": 0}))
            
            # Count projects by status (matching actual data format)
            project_metrics = {
                "Not_Started": sum(1 for p in projects if p.get("project_status") in ["Not Started", "Not_Started"]),
                "ToDo": sum(1 for p in projects if p.get("project_status") in ["ToDo", "To Do"]),
                "In_Progress": sum(1 for p in projects if p.get("project_status") in ["In Progress", "In_Progress"]),
                "Completed": sum(1 for p in projects if p.get("project_status") == "Completed")
            }
            project_metrics["total_projects"] = len(projects)
            
            # Get tasks assigned to or involved with employee (check both username and employee_id)
            tasks_query = {
                "is_deleted": {"$ne": True},
                "$or": [
                    {"assignee": employee_id},
                    {"assignee": current_user.get("username")},
                    {"selected_team_members": {"$in": [employee_id]}},
                    {"selected_team_members": {"$in": [current_user.get("username")]}}
                ]
            }
            
            tasks = list(mongo_db["tasks"].find(tasks_query, {"_id": 0}))
            
            # Count tasks by status (matching actual data format)
            task_metrics = {
                "Pending": sum(1 for t in tasks if t.get("status") in ["Pending", "Pending Review"]),
                "ToDo": sum(1 for t in tasks if t.get("status") in ["ToDo", "To Do"]),
                "In_Progress": sum(1 for t in tasks if t.get("status") in ["In Progress", "In_Progress"]),
                "Completed": sum(1 for t in tasks if t.get("status") == "Completed")
            }
            task_metrics["total_tasks"] = len(tasks)
            
            # Total completed tasks
            total_completed_tasks = task_metrics["Completed"]
            
            return {
                "source": "mongo",
                "employee_id": employee_id,
                "projects": project_metrics,
                "tasks": task_metrics,
                "total_completed_tasks": total_completed_tasks
            }
        
        # Fallback to MySQL
        from app.models.mysql_models import Project, Task
        
        # Get projects where employee is admin or team member (check both username and employee_id)
        projects = db.query(Project).filter(
            and_(
                Project.is_deleted == False,
                or_(
                    Project.project_admins.contains([employee_id]),
                    Project.selected_team_members.contains([employee_id]),
                    Project.project_admins.contains([current_user.get("username")]),
                    Project.selected_team_members.contains([current_user.get("username")])
                )
            )
        ).all()
        
        # Count projects by status (matching actual data format)
        project_metrics = {
            "Not_Started": sum(1 for p in projects if p.project_status in ["Not Started", "Not_Started"]),
            "ToDo": sum(1 for p in projects if p.project_status in ["ToDo", "To Do"]),
            "In_Progress": sum(1 for p in projects if p.project_status in ["In Progress", "In_Progress"]),
            "Completed": sum(1 for p in projects if p.project_status == "Completed")
        }
        project_metrics["total_projects"] = len(projects)
        
        # Get tasks assigned to or involved with employee (check both username and employee_id)
        tasks = db.query(Task).filter(
            and_(
                Task.is_deleted == False,
                or_(
                    Task.assignee == employee_id,
                    Task.assignee == current_user.get("username"),
                    Task.selected_team_members.contains([employee_id]),
                    Task.selected_team_members.contains([current_user.get("username")])
                )
            )
        ).all()
        
        # Count tasks by status (matching actual data format)
        task_metrics = {
            "Pending": sum(1 for t in tasks if t.status in ["Pending", "Pending Review"]),
            "ToDo": sum(1 for t in tasks if t.status in ["ToDo", "To Do"]),
            "In_Progress": sum(1 for t in tasks if t.status in ["In Progress", "In_Progress"]),
            "Completed": sum(1 for t in tasks if t.status == "Completed")
        }
        task_metrics["total_tasks"] = len(tasks)
        
        # Total completed tasks
        total_completed_tasks = task_metrics["Completed"]
        
        return {
            "source": "mysql",
            "employee_id": employee_id,
            "projects": project_metrics,
            "tasks": task_metrics,
            "total_completed_tasks": total_completed_tasks
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching employee metrics: {str(e)}")


@router.get("/departments", response_model=dict)
def get_departments(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get all departments"""
    try:
        departments = [
            {"id": 1, "name": "HR", "description": "Human resources and talent management"},
            {"id": 2, "name": "ADMIN", "description": "Administrative services and management"},
            {"id": 3, "name": "LEGAL", "description": "Legal affairs and compliance"},
            {"id": 4, "name": "TENDERS", "description": "Procurement and tender management"},
            {"id": 5, "name": "FINANCE", "description": "Financial planning and accounting"},
            {"id": 6, "name": "IT", "description": "Information technology and systems"},
            {"id": 7, "name": "OPERATIONS", "description": "Business operations and process management"},
            {"id": 8, "name": "STORES", "description": "Inventory and warehouse management"},
            {"id": 9, "name": "PURCHASE", "description": "Procurement and purchasing operations"}
        ]
        
        return {
            "departments": departments,
            "total": len(departments)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching departments: {str(e)}")

# ADMIN MANAGEMENT API
@router.get("/admins", response_model=dict)
def get_admins(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get all admins from database with profile information"""
    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            admins = list(mongo_db["users"].find(
                {"role": "Admin"},
                {"_id": 0, "username": 1, "email": 1, "firstname": 1, "lastname": 1, "profile_image": 1, "role": 1, "department": 1, "employee_id": 1}
            ))
            if admins:
                return {
                    "source": "mongo",
                    "admins": admins,
                    "total": len(admins)
                }

        # Fallback to MySQL
        from app.models.mysql_models import User
        admins = db.query(User).filter(
            User.role == "Admin"
        ).all()
        
        admin_list = []
        for admin in admins:
            admin_dict = {
                "employee_id": admin.employee_id,
                "username": admin.username,
                "firstname": admin.firstname,
                "lastname": admin.lastname,
                "email": admin.email,
                "profile_image": admin.profile_image,
                "role": admin.role,
                "department": admin.department
            }
            admin_list.append(admin_dict)
        
        return {
            "source": "mysql",
            "admins": admin_list,
            "total": len(admin_list)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching admins: {str(e)}")


# EMPLOYEE MANAGEMENT API
@router.get("/employees", response_model=dict)
def get_employees(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get all employees from database"""
    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            employees = list(mongo_db["users"].find(
                {"role": "Employee"},
                {"_id": 0, "username": 1, "email": 1, "role": 1, "department": 1}
            ))
            if employees:
                return {
                    "source": "mongo",
                    "employees": employees,
                    "total": len(employees)
                }

        # Fallback to MySQL
        from app.models.mysql_models import User
        employees = db.query(User).filter(User.role == "Employee").all()
        
        employee_list = []
        for employee in employees:
            employee_dict = {
                "username": employee.username,
                "email": employee.email,
                "role": employee.role,
                "department": employee.department
            }
            employee_list.append(employee_dict)
        
        return {
            "source": "mysql",
            "employees": employee_list,
            "total": len(employee_list)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching employees: {str(e)}")


# TEAM MANAGEMENT APIs
@router.post("/teams", response_model=dict)
def create_team(
    team_data: dict,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Create new team"""
    try:
        # Check if user is Admin or SuperAdmin
        if current_user.get("role") not in ("Admin", "SuperAdmin"):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Predefined teams based on departments
        teams = [
            {"id": 1, "name": "HR Team", "description": "Human resources and talent management team", "department": "HR"},
            {"id": 2, "name": "Admin Team", "description": "Administrative services and management team", "department": "ADMIN"},
            {"id": 3, "name": "Legal Team", "description": "Legal affairs and compliance team", "department": "LEGAL"},
            {"id": 4, "name": "Tenders Team", "description": "Procurement and tender management team", "department": "TENDERS"},
            {"id": 5, "name": "Finance Team", "description": "Financial planning and accounting team", "department": "FINANCE"},
            {"id": 6, "name": "IT Team", "description": "Information technology and systems team", "department": "IT"},
            {"id": 7, "name": "Operations Team", "description": "Business operations and process management team", "department": "OPERATIONS"},
            {"id": 8, "name": "Stores Team", "description": "Inventory and warehouse management team", "department": "STORES"},
            {"id": 9, "name": "Purchase Team", "description": "Procurement and purchasing operations team", "department": "PURCHASE"}
        ]
        
        return {
            "message": "Teams created successfully",
            "teams": teams
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating teams: {str(e)}")


@router.get("/teams", response_model=dict)
def get_teams(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get all teams"""
    try:
        teams = [
            {"id": 1, "name": "HR Team", "description": "Human resources and talent management team", "department": "HR"},
            {"id": 2, "name": "Admin Team", "description": "Administrative services and management team", "department": "ADMIN"},
            {"id": 3, "name": "Legal Team", "description": "Legal affairs and compliance team", "department": "LEGAL"},
            {"id": 4, "name": "Tenders Team", "description": "Procurement and tender management team", "department": "TENDERS"},
            {"id": 5, "name": "Finance Team", "description": "Financial planning and accounting team", "department": "FINANCE"},
            {"id": 6, "name": "IT Team", "description": "Information technology and systems team", "department": "IT"},
            {"id": 7, "name": "Operations Team", "description": "Business operations and process management team", "department": "OPERATIONS"},
            {"id": 8, "name": "Stores Team", "description": "Inventory and warehouse management team", "department": "STORES"},
            {"id": 9, "name": "Purchase Team", "description": "Procurement and purchasing operations team", "department": "PURCHASE"}
        ]
        
        return {
            "teams": teams,
            "total": len(teams)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching teams: {str(e)}")


# Add this endpoint to your projects router

@router.get("/dropdown/projects-for-tasks", response_model=dict)
def get_projects_for_task_dropdown(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all projects with their phases for task dropdown.
    Returns: project_id, project_name, add_project_phase (phases)
    """
    try:
        projects_data = []
        
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            # Build MongoDB query
            mongo_query: dict = {"is_deleted": {"$ne": True}}
            
            # Apply role-based access control
            if current_user["role"] == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
            elif current_user["role"] == "Employee":
                username = current_user["username"]
                mongo_query["$or"] = [
                    {"project_admins": {"$in": [username]}},
                    {"selected_team_members": {"$in": [username]}}
                ]
            
            # Get projects from MongoDB
            mongo_projects = list(
                mongo_db["projects"].find(mongo_query, {"_id": 0})
            )
            
            for project in mongo_projects:
                projects_data.append({
                    "project_id": project.get("id"),
                    "project_name": project.get("project_name"),
                    "phases": project.get("add_project_phase", []),
                    "department": project.get("department")
                })
            
            if projects_data:
                return {
                    "source": "mongo",
                    "total_projects": len(projects_data),
                    "data": projects_data
                }
        
        # Fallback to MySQL
        query = db.query(Project).filter(Project.is_deleted == False)
        query = apply_role_based_filter(query, current_user)
        
        mysql_projects = query.all()
        
        for project in mysql_projects:
            projects_data.append({
                "project_id": project.id,
                "project_name": project.project_name,
                "phases": project.add_project_phase if project.add_project_phase else [],
                "department": project.department
            })
        
        return {
            "source": "mysql",
            "total_projects": len(projects_data),
            "data": projects_data
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching projects for dropdown: {str(e)}")
    



class NotificationSchema(BaseModel):
    id: str
    type: str  # task_assigned, project_assigned, task_updated, project_updated, task_completed, project_completed, chat_message, alert
    title: str
    message: str
    related_id: int
    related_name: str
    status: str  # pending, read
    created_at: datetime
    metadata: Optional[dict] = None


# ==================== GET ALL NOTIFICATIONS ====================

class NotificationSchema(BaseModel):
    id: str
    type: str  # task_assigned, project_assigned, task_updated, project_updated, task_completed, project_completed, chat_message, alert
    title: str
    message: str
    related_id: int
    related_name: str
    status: str  # pending, read
    created_at: datetime
    metadata: Optional[dict] = None


# ==================== GET ALL NOTIFICATIONS ====================

@router.get("/notifications", response_model=dict)
def get_notifications(
    page: int = Query(1, ge=1),
    employee_id: Optional[str] = Query(None, description="Employee ID to view notifications for (Admin/SuperAdmin only)"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Get personalized notifications for current user.
    Shows task assignments, updates, completions, project assignments, updates, 
    chat messages, and deadline alerts only for their work involvement.
    """
    try:
        limit = 20  # Fixed limit per page
        skip = (page - 1) * limit
        notifications = []
        
        # Determine which employee to fetch notifications for
        target_emp_id = current_user.get("employee_id")
        target_user_role = current_user.get("role")
        
        # Allow Admin/SuperAdmin to view other employees' notifications
        if employee_id:
            if current_user["role"] not in ("Admin", "SuperAdmin"):
                raise HTTPException(status_code=403, detail="Only Admin/SuperAdmin can view other employees' notifications")
            
            # Check if employee exists
            emp = db.query(User).filter(User.employee_id == employee_id).first()
            if not emp:
                raise HTTPException(status_code=404, detail="Employee not found")
            
            # Admin can only view their department
            if current_user["role"] == "Admin":
                if emp.department != current_user.get("department"):
                    raise HTTPException(status_code=403, detail="Can only view employees from your department")
            
            target_emp_id = employee_id
            target_user_role = emp.role
        
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            # ==================== TASK NOTIFICATIONS ====================
            
            # 1. Tasks assigned to user (newly assigned)
            new_task_assignments = list(mongo_db["tasks"].find({
                "is_deleted": {"$ne": True},
                "$or": [
                    {"assignee": target_emp_id},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("created_at", -1))
            
            for task in new_task_assignments:
                # Personalized message based on who assigned it
                if task.get("assignee") == target_emp_id:
                    message = f"You have been assigned to task: {task.get('task_title')}"
                else:
                    message = f"You have been added to task: {task.get('task_title')}"
                
                notifications.append({
                    "id": str(task.get("id")),
                    "type": "task_assigned",
                    "title": "Task Assigned",
                    "message": message,
                    "related_id": task.get("id"),
                    "related_name": task.get("task_title"),
                    "status": "pending",
                    "created_at": task.get("created_at", datetime.utcnow()),
                    "metadata": {
                        "priority": task.get("priority"),
                        "status": task.get("status"),
                        "deadline": task.get("deadline")
                    }
                })
            
            # 2. Task status updates (only for assigned tasks)
            task_updates = list(mongo_db["task_activities"].find({
                "task_id": {"$exists": True}
            }, {"_id": 0}).sort("task_status_updation_time_stamp", -1))
            
            for activity in task_updates:
                # Check if user is assigned to this task
                task = mongo_db["tasks"].find_one({"id": activity.get("task_id")}, {"_id": 0})
                if task and (task.get("assignee") == target_emp_id or target_emp_id in (task.get("selected_team_members") or [])):
                    # Personalized message
                    if activity.get("task_completed_by") == target_emp_id:
                        message = f"You updated task '{activity.get('task_title')}' to {task.get('status')}"
                    else:
                        message = f"Task '{activity.get('task_title')}' has been updated to {task.get('status')} by {activity.get('task_completed_by')}"
                    
                    notifications.append({
                        "id": f"activity_{activity.get('task_id')}_{activity.get('task_status_updation_time_stamp')}",
                        "type": "task_updated",
                        "title": "Task Updated",
                        "message": message,
                        "related_id": activity.get("task_id"),
                        "related_name": activity.get("task_title"),
                        "status": "pending",
                        "created_at": activity.get("task_status_updation_time_stamp", datetime.utcnow()),
                        "metadata": {
                            "activity_type": activity.get("activity_type"),
                            "updated_by": activity.get("task_completed_by")
                        }
                    })
            
            # 3. Task completed
            completed_tasks = list(mongo_db["tasks"].find({
                "is_deleted": {"$ne": True},
                "status": "Completed",
                "$or": [
                    {"assignee": target_emp_id},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("updated_at", -1))
            
            for task in completed_tasks:
                notifications.append({
                    "id": f"completed_{task.get('id')}",
                    "type": "task_completed",
                    "title": "Task Completed",
                    "message": f"Task '{task.get('task_title')}' has been completed",
                    "related_id": task.get("id"),
                    "related_name": task.get("task_title"),
                    "status": "pending",
                    "created_at": task.get("updated_at", datetime.utcnow()),
                    "metadata": {"priority": task.get("priority")}
                })
            
            # ==================== PROJECT NOTIFICATIONS ====================
            
            # 4. Projects assigned to user
            assigned_projects = list(mongo_db["projects"].find({
                "is_deleted": {"$ne": True},
                "$or": [
                    {"project_admins": {"$in": [target_emp_id]}},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("created_at", -1))
            
            for project in assigned_projects:
                # Personalized message
                if target_emp_id in (project.get("project_admins") or []):
                    message = f"You have been made admin of project: {project.get('project_name')}"
                else:
                    message = f"You have been assigned to project: {project.get('project_name')}"
                
                notifications.append({
                    "id": str(project.get("id")),
                    "type": "project_assigned",
                    "title": "Project Assigned",
                    "message": message,
                    "related_id": project.get("id"),
                    "related_name": project.get("project_name"),
                    "status": "pending",
                    "created_at": project.get("created_at", datetime.utcnow()),
                    "metadata": {
                        "status": project.get("project_status"),
                        "priority": project.get("priority"),
                        "deadline": project.get("deadline")
                    }
                })
            
            # 5. Project status updates (only for assigned projects)
            project_updates = list(mongo_db["projects"].find({
                "is_deleted": {"$ne": True},
                "$or": [
                    {"project_admins": {"$in": [target_emp_id]}},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("updated_at", -1))
            
            for project in project_updates:
                notifications.append({
                    "id": f"proj_update_{project.get('id')}",
                    "type": "project_updated",
                    "title": "Project Updated",
                    "message": f"Project '{project.get('project_name')}' status is now {project.get('project_status')}",
                    "related_id": project.get("id"),
                    "related_name": project.get("project_name"),
                    "status": "pending",
                    "created_at": project.get("updated_at", datetime.utcnow()),
                    "metadata": {"status": project.get("project_status")}
                })
            
            # 6. Project completed
            completed_projects = list(mongo_db["projects"].find({
                "is_deleted": {"$ne": True},
                "project_status": "Completed",
                "$or": [
                    {"project_admins": {"$in": [target_emp_id]}},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("updated_at", -1))
            
            for project in completed_projects:
                notifications.append({
                    "id": f"proj_completed_{project.get('id')}",
                    "type": "project_completed",
                    "title": "Project Completed",
                    "message": f"Project '{project.get('project_name')}' has been completed",
                    "related_id": project.get("id"),
                    "related_name": project.get("project_name"),
                    "status": "pending",
                    "created_at": project.get("updated_at", datetime.utcnow()),
                    "metadata": {}
                })
            
            # ==================== CHAT NOTIFICATIONS ====================
            
            # 7. Chat messages ONLY from assigned tasks
            assigned_task_ids = [t.get("id") for t in new_task_assignments]
            if assigned_task_ids:
                task_chats = list(mongo_db["task_chats"].find({
                    "task_id": {"$in": [str(tid) for tid in assigned_task_ids]}
                }, {"_id": 0}).sort("timestamp", -1))
                
                for chat in task_chats:
                    if chat.get("sender_id") != target_emp_id:  # Don't notify for own messages
                        notifications.append({
                            "id": f"chat_task_{chat.get('timestamp')}",
                            "type": "chat_message",
                            "title": "New Chat Message",
                            "message": f"{chat.get('sender_name')} commented: {chat.get('message')[:50]}...",
                            "related_id": int(chat.get("task_id")),
                            "related_name": f"Task Comment",
                            "status": "pending",
                            "created_at": chat.get("timestamp", datetime.utcnow()),
                            "metadata": {
                                "sender": chat.get("sender_name"),
                                "message": chat.get("message")
                            }
                        })
            
            # 8. Chat messages ONLY from assigned projects
            assigned_project_ids = [p.get("id") for p in assigned_projects]
            if assigned_project_ids:
                project_chats = list(mongo_db["project_chats"].find({
                    "project_id": {"$in": assigned_project_ids}
                }, {"_id": 0}).sort("timestamp", -1))
                
                for chat in project_chats:
                    if chat.get("sender_id") != target_emp_id:  # Don't notify for own messages
                        notifications.append({
                            "id": f"chat_project_{chat.get('timestamp')}",
                            "type": "chat_message",
                            "title": "New Project Chat",
                            "message": f"{chat.get('sender_name')} commented: {chat.get('message')[:50]}...",
                            "related_id": chat.get("project_id"),
                            "related_name": f"Project Comment",
                            "status": "pending",
                            "created_at": chat.get("timestamp", datetime.utcnow()),
                            "metadata": {
                                "sender": chat.get("sender_name"),
                                "message": chat.get("message")
                            }
                        })
            
            # ==================== ALERTS ====================
            
            # 9. Upcoming deadlines (tasks)
            upcoming_tasks = list(mongo_db["tasks"].find({
                "is_deleted": {"$ne": True},
                "status": {"$ne": "Completed"},
                "deadline": {
                    "$lte": datetime.utcnow() + timedelta(days=3),
                    "$gte": datetime.utcnow()
                },
                "$or": [
                    {"assignee": target_emp_id},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("deadline", 1))
            
            for task in upcoming_tasks:
                days_left = (task.get("deadline") - datetime.utcnow()).days
                notifications.append({
                    "id": f"alert_task_{task.get('id')}",
                    "type": "alert",
                    "title": "Task Deadline Alert",
                    "message": f"Your task '{task.get('task_title')}' is due in {days_left} day(s)",
                    "related_id": task.get("id"),
                    "related_name": task.get("task_title"),
                    "status": "pending",
                    "created_at": datetime.utcnow(),
                    "metadata": {
                        "deadline": task.get("deadline"),
                        "days_remaining": days_left
                    }
                })
            
            # 10. Upcoming deadlines (projects)
            upcoming_projects = list(mongo_db["projects"].find({
                "is_deleted": {"$ne": True},
                "project_status": {"$ne": "Completed"},
                "deadline": {
                    "$lte": datetime.utcnow() + timedelta(days=7),
                    "$gte": datetime.utcnow()
                },
                "$or": [
                    {"project_admins": {"$in": [target_emp_id]}},
                    {"selected_team_members": {"$in": [target_emp_id]}}
                ]
            }, {"_id": 0}).sort("deadline", 1))
            
            for project in upcoming_projects:
                days_left = (project.get("deadline") - datetime.utcnow()).days
                notifications.append({
                    "id": f"alert_project_{project.get('id')}",
                    "type": "alert",
                    "title": "Project Deadline Alert",
                    "message": f"Your project '{project.get('project_name')}' is due in {days_left} day(s)",
                    "related_id": project.get("id"),
                    "related_name": project.get("project_name"),
                    "status": "pending",
                    "created_at": datetime.utcnow(),
                    "metadata": {
                        "deadline": project.get("deadline"),
                        "days_remaining": days_left
                    }
                })
        
        # Sort by created_at descending
        notifications = sorted(notifications, key=lambda x: x["created_at"], reverse=True)
        
        # Count unread (before pagination)
        unread_count = len([n for n in notifications if n["status"] == "pending"])
        total_count = len(notifications)
        
        # Apply pagination
        paginated_notifications = notifications[skip:skip + limit]
        
        return {
            "source": "mongo",
            "employee_id": target_emp_id,
            "page": page,
            "page_size": limit,
            "total_pages": (total_count + limit - 1) // limit,
            "total_notifications": total_count,
            "unread_count": unread_count,
            "notifications": paginated_notifications
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")
    


class CustomField(BaseModel):
    name: str
    value: str


class EmployeeImportItem(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    phone: str
    username: str
    employee_id: str
    designation: str
    department: str
    role: str
    status: str
    blood_group: str
    joining_date: str
    date_of_birth: str
    permanent_address: str
    current_address: str
    emergency_contact_name: str
    emergency_contact_number: str
    emergency_contact_relation: str
    profile_image: Optional[str] = None
    custom_fields: Optional[Union[List[dict], List[CustomField]]] = []

    class Config:
        arbitrary_types_allowed = True


class BulkImportRequest(BaseModel):
    employees: List[EmployeeImportItem]

    @validator('employees')
    def validate_employee_count(cls, v):
        if len(v) > 1000:
            raise ValueError('Maximum 1000 employees can be imported at once')
        if len(v) == 0:
            raise ValueError('At least 1 employee is required')
        return v


class ImportResponse(BaseModel):
    success: bool
    total_rows: int
    successful_imports: int
    failed_imports: int
    errors: List[dict]
    imported_employees: List[str]



async def bulk_import_employees(
    request: BulkImportRequest,
    db: Session,
    current_user: dict
) -> ImportResponse:
    """
    Shared logic for bulk importing employees from Excel or JSON.
    Handles MongoDB and MySQL insertion, and returns ImportResponse.
    """

    successful_imports = []
    failed_imports = []
    errors = []
    imported_employee_ids = []

    mongo_client = get_mongo_client()
    mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None

    for index, employee_data in enumerate(request.employees):
        row_number = index + 1

        try:
            # Convert Pydantic model to dict
            employee_dict = employee_data.dict()

            # Restrict Admin imports to their own department
            if current_user["role"] == "Admin":
                if employee_dict['department'] != current_user.get('department'):
                    raise ValueError(
                        f"Admin can only import employees to their own department "
                        f"({current_user.get('department')})"
                    )

            employee_id = str(employee_dict.get("employee_id")).strip()
            username = str(employee_dict.get("username")).strip()
            email = str(employee_dict.get("email")).strip()

            # 🔹 Check duplicates in MySQL
            try:
                existing_user_count = db.query(User).filter(
                    (User.employee_id == employee_id) |
                    (User.username == username) |
                    (User.email == email)
                ).count()

                if existing_user_count > 0:
                    raise ValueError(
                        f"Duplicate entry found for Employee ID '{employee_id}' or Username/Email"
                    )
            except Exception as e:
                print(f"MySQL duplicate check error: {e}")
                raise

            # 🔹 Check duplicates in MongoDB
            if mongo_db is not None:
                existing_mongo = mongo_db["users"].find_one({
                    "$or": [
                        {"employee_id": employee_id},
                        {"username": username},
                        {"email": email}
                    ]
                })
                if existing_mongo:
                    raise ValueError(f"Employee '{employee_id}' already exists in MongoDB")

            # 🔹 Generate password
            plain_password = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            employee_dict['password'] = hash_password(plain_password)

            # 🔹 Convert custom_fields to JSON string for MySQL
            if employee_dict.get('custom_fields'):
                employee_dict['custom_fields'] = json.dumps(employee_dict['custom_fields'])

            # 🔹 Insert into MongoDB
            if mongo_db is not None:
                mongo_payload = dict(employee_dict)
                mongo_db["users"].insert_one(mongo_payload)

            # 🔹 Insert into MySQL
            mysql_payload = dict(employee_dict)
            mysql_payload.pop('_id', None)
            mysql_payload.pop('_cls', None)

            new_user = User(**mysql_payload)
            db.add(new_user)
            db.commit()

            # 🔹 Send credentials email
            try:
                send_user_credentials_email(
                    to_email=email,
                    username=username,
                    password=plain_password
                )
            except Exception as e:
                print(f"Email sending failed for {email}: {e}")

            successful_imports.append(username)
            imported_employee_ids.append(employee_id)

        except ValueError as e:
            failed_imports.append(row_number)
            errors.append({
                "row": row_number,
                "employee_id": getattr(employee_data, "employee_id", "N/A"),
                "errors": [{"field": "validation", "message": str(e)}],
            })

        except Exception as e:
            failed_imports.append(row_number)
            errors.append({
                "row": row_number,
                "employee_id": getattr(employee_data, "employee_id", "N/A"),
                "errors": [{"field": "general", "message": str(e)}],
            })
            db.rollback()

    return ImportResponse(
        success=len(failed_imports) == 0,
        total_rows=len(request.employees),
        successful_imports=len(successful_imports),
        failed_imports=len(failed_imports),
        errors=errors,
        imported_employees=imported_employee_ids
    )


# ---------------------------
# Excel Import Endpoint
# ---------------------------

@router.post("/employees/import-excel", response_model=ImportResponse)
async def import_employees_from_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Bulk import employees from Excel file (up to 1000 records)
    Supports user-friendly Excel headers (e.g., "First Name", "Employee ID")
    """

    # Role validation
    if current_user["role"] not in ["SuperAdmin", "Admin"]:
        raise HTTPException(status_code=403, detail="Only SuperAdmin and Admin can import employees")

    # File validation
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Only .xlsx and .xls files are supported"
        )

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))

        if df.empty:
            raise HTTPException(status_code=400, detail="Excel file is empty")

        if len(df) > 1000:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum 1000 rows allowed. Your file has {len(df)} rows"
            )

        # 🔹 Normalize column names
        df.columns = (
            df.columns
            .str.strip()
            .str.lower()
            .str.replace(' ', '_')
            .str.replace('-', '_')
        )

        # 🔹 Map Excel-friendly names to model field names
        column_mapping = {
            "first_name": "firstname",
            "last_name": "lastname",
            "email": "email",
            "phone": "phone",
            "username": "username",
            "employee_id": "employee_id",
            "designation": "designation",
            "department": "department",
            "role": "role",
            "status": "status",
            "blood_group": "blood_group",
            "joining_date": "joining_date",
            "date_of_birth": "date_of_birth",
            "profile_image": "profile_image",
            "permanent_address": "permanent_address",
            "current_address": "current_address",
            "emergency_contact_name": "emergency_contact_name",
            "emergency_contact_number": "emergency_contact_number",
            "emergency_contact_relation": "emergency_contact_relation"
        }
        df.rename(columns=column_mapping, inplace=True)

        # Replace NaN with None
        df = df.where(pd.notna(df), None)

        # 🔹 Convert all data to strings (fix int, float, Timestamp issues)
        for col in df.columns:
            df[col] = df[col].apply(
                lambda x: str(x).strip() if x not in [None, "None", "nan", "NaT"] else None
            )

        # 🔹 Format date fields as YYYY-MM-DD (optional)
        for date_col in ["joining_date", "date_of_birth"]:
            if date_col in df.columns:
                try:
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce').dt.strftime('%Y-%m-%d')
                except Exception:
                    pass

        # 🔹 Convert DataFrame to list of dicts
        employees_data = df.to_dict('records')

        # ✅ Validate with Pydantic
        request = BulkImportRequest(employees=employees_data)

        # ✅ Continue with shared import logic
        return await bulk_import_employees(
            request=request,
            db=db,
            current_user=current_user
        )

    except pd.errors.ParserError:
        raise HTTPException(status_code=400, detail="Unable to parse Excel file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
