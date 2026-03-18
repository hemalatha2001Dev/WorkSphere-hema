
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.db.mongo import get_mongo_client
from app.core.config import settings


def apply_role_based_filter_projects(query, current_user: dict):
    """Apply role-based filtering to project queries using employee_id"""
    from app.models.mysql_models import Project
    
    role = current_user.get("role")
    department = current_user.get("department")
    employee_id = current_user.get("employee_id")
    
    if role == "Admin" and department:
        # Admin can only see projects from their department
        query = query.filter(Project.department == department)
    elif role == "Employee" and employee_id:
        # Employee can only see projects they're assigned to (by employee_id)
        query = query.filter(
            or_(
                Project.project_admins.contains([employee_id]),
                Project.selected_team_members.contains([employee_id])
            )
        )
    # SuperAdmin can see all projects (no filter needed)
    
    return query


def apply_role_based_filter_tasks(query, current_user: dict):
    """Apply role-based filtering to task queries using employee_id"""
    from app.models.mysql_models import Task
    
    role = current_user.get("role")
    department = current_user.get("department")
    employee_id = current_user.get("employee_id")
    
    if role == "Admin" and department:
        # Admin can only see tasks from their department
        query = query.filter(Task.department == department)
    elif role == "Employee" and employee_id:
        # Employee can only see tasks they're assigned to (by employee_id)
        query = query.filter(
            or_(
                Task.assignee == employee_id,
                Task.selected_team_members.contains([employee_id])
            )
        )
    # SuperAdmin can see all tasks (no filter needed)
    
    return query

def apply_role_based_filter_mongo_projects(mongo_query: dict, current_user: dict):
    """Apply role-based filtering to MongoDB project queries using employee_id and username"""
    role = current_user.get("role")
    department = current_user.get("department")
    employee_id = current_user.get("employee_id")
    username = current_user.get("username")
    
    if role == "Admin" and department:
        # Admin can only see projects from their department
        mongo_query["department"] = department
    elif role == "Employee" and (employee_id or username):
        # Employee can only see projects they're assigned to (by employee_id or username)
        existing_filter = mongo_query.copy()
        or_conditions = []
        
        if employee_id:
            or_conditions.append({"project_admins": {"$in": [employee_id]}})
            or_conditions.append({"selected_team_members": {"$in": [employee_id]}})
        
        if username:
            or_conditions.append({"project_admins": {"$in": [username]}})
            or_conditions.append({"selected_team_members": {"$in": [username]}})
        
        mongo_query = {
            "$and": [
                existing_filter,
                {"$or": or_conditions}
            ]
        }
    # SuperAdmin can see all projects (no filter needed)
    
    return mongo_query


def apply_role_based_filter_mongo_tasks(mongo_query: dict, current_user: dict):
    """Apply role-based filtering to MongoDB task queries using employee_id and username"""
    role = current_user.get("role")
    department = current_user.get("department")
    employee_id = current_user.get("employee_id")
    username = current_user.get("username")
    
    if role == "Admin" and department:
        # Admin can only see tasks from their department
        mongo_query["department"] = department
    elif role == "Employee" and (employee_id or username):
        # Employee can only see tasks they're assigned to (by employee_id or username)
        existing_filter = mongo_query.copy()
        or_conditions = []
        
        if employee_id:
            or_conditions.append({"assignee": employee_id})
            or_conditions.append({"selected_team_members": {"$in": [employee_id]}})
        
        if username:
            or_conditions.append({"assignee": username})
            or_conditions.append({"selected_team_members": {"$in": [username]}})
        
        mongo_query = {
            "$and": [
                existing_filter,
                {"$or": or_conditions}
            ]
        }
    # SuperAdmin can see all tasks (no filter needed)
    
    return mongo_query