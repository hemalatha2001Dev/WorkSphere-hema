from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import json
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
    WebSocket,
    WebSocketDisconnect
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import ReturnDocument
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.filters import (
    apply_role_based_filter_mongo_tasks,
    apply_role_based_filter_tasks,
)
from app.core.security import get_current_user
from app.db.mongo import get_mongo_client
from app.db.mysql import get_mysql_session
from app.models.mysql_models import Task, TaskActivity, TaskChat, User
from app.schemas.user_schema import (
    AlertSchema,
    CustomField,
    DashboardMetricsSchema,
    PieChartDistributionSchema,
    RecentTaskSchema,
    TaskChatResponseSchema,
    TaskChatSchema,
    TaskCreateSchema,
    TaskResponseSchema,
    TaskUpdateSchema,
    TeamActivitySchema,
    TimeRangeSchema,
    WeekWiseDistributionSchema,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


def create_task_mongo(task_data: dict):
    """Create task in MongoDB"""
    client = get_mongo_client()
    if client:
        db = client[settings.MONGO_DB_NAME]
        result = db["tasks"].insert_one(task_data)
        return str(result.inserted_id)
    return None




# ---------- Connection Manager for Task Chat ----------
class TaskConnectionManager:
    def __init__(self):
        # map task_id (str or int) -> list[WebSocket]
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()
        key = str(task_id)
        if key not in self.active_connections:
            self.active_connections[key] = []
        self.active_connections[key].append(websocket)
        print(f"✅ Client connected to Task {key} | total: {len(self.active_connections[key])}")

    def disconnect(self, websocket: WebSocket, task_id: str):
        key = str(task_id)
        if key in self.active_connections and websocket in self.active_connections[key]:
            self.active_connections[key].remove(websocket)
            if not self.active_connections[key]:
                del self.active_connections[key]
        print(f"❌ Client disconnected from Task {key}")

    async def broadcast(self, message: dict, task_id: str):
        key = str(task_id)
        if key not in self.active_connections:
            return
        for conn in list(self.active_connections[key]):
            try:
                await conn.send_json(message)
            except Exception as e:
                # if send fails, just log and continue
                print(f"⚠️ Error sending to Task {key} client: {e}")

# create a single manager instance (module-level)
task_manager = TaskConnectionManager()



# ==================== DASHBOARD ENDPOINTS ====================

@router.get("/dashboard/metrics", response_model=DashboardMetricsSchema)
def get_dashboard_metrics(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get dashboard key metrics including total, completed, in-progress, todo, and pending tasks."""
    try:
        # Check MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query = {"is_deleted": {"$ne": True}}
            
            # Apply role-based filter
            mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)
            
            # Apply date filter if provided
            if start_date and end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    mongo_query["created_at"] = {
                        "$gte": start_dt,
                        "$lte": end_dt
                    }
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail="Invalid date format. Use ISO 8601."
                    )
            
            mongo_tasks = list(mongo_db["tasks"].find(mongo_query, {"_id": 0}))
            
            if mongo_tasks:
                # Count statuses from MongoDB
                total_tasks = len(mongo_tasks)
                completed = 0
                in_progress = 0
                todo = 0
                pending = 0

                for task in mongo_tasks:
                    # If task is in Pending Review, use the pending_status for counting
                    if task.get("status") == "Pending Review" and task.get("pending_status"):
                        display_status = task.get("pending_status")
                    else:
                        display_status = task.get("status")
                    
                    if display_status == "Completed":
                        completed += 1
                    elif display_status in ["In Progress", "In_Progress"]:
                        in_progress += 1
                    elif display_status in ["ToDo", "To Do"]:
                        todo += 1
                    elif display_status in ["Pending", "Pending Review"]:
                        pending += 1

                return {
                    "total_tasks": total_tasks,
                    "completed": completed,
                    "in_progress": in_progress,
                    "todo": todo,
                    "pending": pending,
                }
        
        # Fallback to MySQL
        base_query = db.query(Task).filter(Task.is_deleted == False)
        base_query = apply_role_based_filter_tasks(base_query, current_user)

        # Apply date filter if provided
        if start_date and end_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                base_query = base_query.filter(
                    Task.created_at.between(start_dt, end_dt)
                )
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid date format. Use ISO 8601."
                )

        # Fetch all tasks
        all_tasks = base_query.all()
        
        # Count statuses accounting for pending_status when status is "Pending Review"
        total_tasks = len(all_tasks)
        completed = 0
        in_progress = 0
        todo = 0
        pending = 0

        for task in all_tasks:
            # If task is in Pending Review, use the updated_status for counting
            if hasattr(task, 'updated_status') and task.status == "Pending Review" and task.updated_status:
                display_status = task.updated_status
            else:
                display_status = task.status
            
            if display_status == "Completed":
                completed += 1
            elif display_status in ["In Progress", "In_Progress"]:
                in_progress += 1
            elif display_status in ["ToDo", "To Do"]:
                todo += 1
            elif display_status in ["Pending", "Pending Review"]:
                pending += 1

        return {
            "total_tasks": total_tasks,
            "completed": completed,
            "in_progress": in_progress,
            "todo": todo,
            "pending": pending,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching dashboard metrics: {str(e)}"
        )


@router.get(
    "/dashboard/pie-chart-distribution", response_model=PieChartDistributionSchema
)
def get_pie_chart_distribution(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get task distribution for pie chart, focusing on incomplete and completed statuses."""
    try:
        # Check MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query = {"is_deleted": {"$ne": True}}
            
            # Apply role-based filter
            mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)
            
            # Apply date filter if provided
            if start_date and end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    mongo_query["created_at"] = {
                        "$gte": start_dt,
                        "$lte": end_dt
                    }
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail="Invalid date format. Use ISO 8601."
                    )
            
            mongo_tasks = list(mongo_db["tasks"].find(mongo_query, {"_id": 0}))
            
            if mongo_tasks:
                # Count statuses from MongoDB
                in_progress = 0
                completed = 0
                pending = 0
                todo = 0

                for task in mongo_tasks:
                    # If task is in Pending Review, use the pending_status for counting
                    if task.get("status") == "Pending Review" and task.get("pending_status"):
                        display_status = task.get("pending_status")
                    else:
                        display_status = task.get("status")
                    
                    if display_status == "Completed":
                        completed += 1
                    elif display_status in ["In Progress", "In_Progress"]:
                        in_progress += 1
                    elif display_status in ["ToDo", "To Do"]:
                        todo += 1
                    elif display_status in ["Pending", "Pending Review"]:
                        pending += 1

                return {
                    "in_progress": in_progress,
                    "completed": completed,
                    "pending": pending,
                    "todo": todo,
                }
        
        # Fallback to MySQL
        base_query = db.query(Task).filter(Task.is_deleted == False)
        base_query = apply_role_based_filter_tasks(base_query, current_user)

        # Apply date filter if provided
        if start_date and end_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                base_query = base_query.filter(
                    Task.created_at.between(start_dt, end_dt)
                )
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid date format. Use ISO 8601."
                )

        # Fetch all tasks
        all_tasks = base_query.all()
        
        # Count statuses accounting for pending_status when status is "Pending Review"
        in_progress = 0
        completed = 0
        pending = 0
        todo = 0

        for task in all_tasks:
            # If task is in Pending Review, use the updated_status for counting
            if hasattr(task, 'updated_status') and task.status == "Pending Review" and task.updated_status:
                display_status = task.updated_status
            else:
                display_status = task.status
            
            if display_status == "Completed":
                completed += 1
            elif display_status in ["In Progress", "In_Progress"]:
                in_progress += 1
            elif display_status in ["ToDo", "To Do"]:
                todo += 1
            elif display_status in ["Pending", "Pending Review"]:
                pending += 1

        return {
            "in_progress": in_progress,
            "completed": completed,
            "pending": pending,
            "todo": todo,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching pie chart data: {str(e)}"
        )


@router.get(
    "/dashboard/week-wise-distribution", response_model=List[WeekWiseDistributionSchema]
)
def get_week_wise_distribution(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Get week-wise distribution of completed and in-progress tasks.
    """
    try:
        # Check MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            mongo_query = {"is_deleted": {"$ne": True}}
            mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)
            
            mongo_tasks = list(mongo_db["tasks"].find(mongo_query, {"_id": 0}))
            
            if mongo_tasks:
                today = datetime.now()
                data = []

                for i in range(4):  # Last 4 weeks
                    start_of_week = today - timedelta(weeks=i + 1)
                    end_of_week = today - timedelta(weeks=i)

                    # Filter tasks for this week
                    week_tasks = [
                        t for t in mongo_tasks
                        if start_of_week <= datetime.fromisoformat(str(t.get("created_at")).replace("Z", "+00:00")) <= end_of_week
                    ]

                    # Count statuses
                    completed_count = 0
                    in_progress_count = 0

                    for task in week_tasks:
                        if task.get("status") == "Pending Review" and task.get("pending_status"):
                            display_status = task.get("pending_status")
                        else:
                            display_status = task.get("status")
                        
                        if display_status == "Completed":
                            completed_count += 1
                        elif display_status in ["In Progress", "In_Progress"]:
                            in_progress_count += 1

                    data.append(
                        {
                            "week_label": f"{start_of_week.strftime('%b %d')} - {end_of_week.strftime('%b %d')}",
                            "completed": completed_count,
                            "in_progress": in_progress_count,
                        }
                    )

                return data
        
        # Fallback to MySQL
        base_query = db.query(Task).filter(Task.is_deleted == False)
        base_query = apply_role_based_filter_tasks(base_query, current_user)

        today = datetime.now()
        data = []

        for i in range(4):  # Last 4 weeks
            start_of_week = today - timedelta(weeks=i + 1)
            end_of_week = today - timedelta(weeks=i)

            # Fetch tasks for this week
            week_tasks = base_query.filter(
                Task.created_at.between(start_of_week, end_of_week)
            ).all()

            # Count statuses accounting for pending_status when status is "Pending Review"
            completed_count = 0
            in_progress_count = 0

            for task in week_tasks:
                # If task is in Pending Review, use the updated_status for counting
                if hasattr(task, 'updated_status') and task.status == "Pending Review" and task.updated_status:
                    display_status = task.updated_status
                else:
                    display_status = task.status
                
                if display_status == "Completed":
                    completed_count += 1
                elif display_status in ["In Progress", "In_Progress"]:
                    in_progress_count += 1

            data.append(
                {
                    "week_label": f"{start_of_week.strftime('%b %d')} - {end_of_week.strftime('%b %d')}",
                    "completed": completed_count,
                    "in_progress": in_progress_count,
                }
            )

        return data
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching week-wise distribution: {str(e)}"
        )
    
@router.get("/dashboard/recent-tasks", response_model=List[RecentTaskSchema])
def get_recent_tasks(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get recent tasks"""
    try:
        base_query = db.query(Task).filter(Task.is_deleted == False)
        base_query = apply_role_based_filter_tasks(base_query, current_user)

        tasks = base_query.order_by(Task.updated_at.desc()).limit(limit).all()

        recent_tasks = []
        for task in tasks:
            recent_tasks.append(
                {
                    "task_name": task.task_title,
                    "task_status": task.status,
                    "task_progress_percentage": task.progress_percentage,
                }
            )

        return recent_tasks
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching recent tasks: {str(e)}"
        )


@router.get("/dashboard/team-activity", response_model=List[TeamActivitySchema])
def get_team_activity(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get team activity feed"""
    try:
        base_query = db.query(TaskActivity)

        # Join with tasks for role-based filtering
        if current_user["role"] == "Admin" and current_user.get("department"):
            base_query = base_query.join(Task).filter(
                Task.department == current_user["department"]
            )
        elif current_user["role"] == "Employee" and current_user.get("username"):
            base_query = base_query.join(Task).filter(
                or_(
                    Task.assignee == current_user["username"],
                    Task.selected_team_members.contains([current_user["username"]]),
                )
            )

        activities = (
            base_query.order_by(TaskActivity.task_status_updation_time_stamp.desc())
            .limit(limit)
            .all()
        )

        team_activities = []
        for activity in activities:
            team_activities.append(
                {
                    "task_name": activity.task_name,
                    "task_completed_by": activity.task_completed_by or "System",
                    "task_title": activity.task_title,
                    "task_description": activity.task_description,
                    "task_status_updation_time_stamp": activity.task_status_updation_time_stamp,
                }
            )

        return team_activities
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching team activity: {str(e)}"
        )


# ==================== TASK CRUD ENDPOINTS ====================


@router.post("/", response_model=dict)
def create_task(
    task: TaskCreateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Create new task with custom fields"""
    try:
        task_data = task.dict()
        task_data["created_by"] = current_user["username"]
        task_data["department"] = current_user.get("department")

        # Handle deadline conversion
        if task_data.get("deadline"):
            try:
                task_data["deadline"] = datetime.fromisoformat(
                    task_data["deadline"].replace("Z", "+00:00")
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid deadline format")

        # Convert custom_fields to list of dicts if provided
        custom_fields = task_data.get("custom_fields", [])
        if custom_fields:
            task_data["custom_fields"] = [
                field.dict() if isinstance(field, CustomField) else field
                for field in custom_fields
            ]
        else:
            task_data["custom_fields"] = []

        # Create in MySQL FIRST to get the auto-increment ID
        mysql_task_data = {
            "task_title": task_data.get("task_title"),
            "description": task_data.get("description"),
            "project_lists": task_data.get("project_lists", []),
            "project_phases": task_data.get("project_phases", []),
            "status": task_data.get("status", "Pending"),
            "priority": task_data.get("priority", "Medium"),
            "assignee": task_data.get("assignee"),
            "selected_team_members": task_data.get("selected_team_members", []),
            "select_time_range": task_data.get("select_time_range"),
            "tasks_files": task_data.get("tasks_files", []),
            "custom_fields": task_data.get("custom_fields", []),  # NEW: Custom fields
            "progress_percentage": 0,
            "created_by": task_data.get("created_by"),
            "department": task_data.get("department"),
            "deadline": task_data.get("deadline"),
            "is_deleted": False,
        }

        mysql_task = Task(**mysql_task_data)
        db.add(mysql_task)
        db.commit()
        db.refresh(mysql_task)

        mysql_task_id = mysql_task.id

        # Create in MongoDB using the same ID
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            mongo_task = dict(task_data)
            mongo_task["id"] = mysql_task_id
            mongo_task["project_lists"] = task_data.get("project_lists", [])
            mongo_task["selected_team_members"] = task_data.get(
                "selected_team_members", []
            )
            mongo_task["tasks_files"] = task_data.get("tasks_files", [])
            mongo_task["custom_fields"] = task_data.get(
                "custom_fields", []
            )  # NEW: Custom fields
            mongo_task.setdefault("created_at", datetime.utcnow())
            mongo_task.setdefault("updated_at", datetime.utcnow())
            mongo_task.setdefault("is_deleted", False)
            mongo_db["tasks"].insert_one(mongo_task)

            # Update counter to match MySQL ID
            mongo_db["counters"].update_one(
                {"_id": "task_id"}, {"$set": {"seq": mysql_task_id}}, upsert=True
            )

            # Log activity in Mongo
            try:
                mongo_db["task_activities"].insert_one(
                    {
                        "task_id": mysql_task_id,
                        "task_name": task_data.get("task_title"),
                        "task_completed_by": current_user["username"],
                        "task_title": task_data.get("task_title"),
                        "task_description": task_data.get("description"),
                        "activity_type": "created",
                        "task_status_updation_time_stamp": datetime.utcnow(),
                    }
                )
            except Exception:
                pass

        # Log activity in MySQL
        activity = TaskActivity(
            task_id=mysql_task.id,
            task_name=mysql_task.task_title,
            task_completed_by=current_user["username"],
            task_title=mysql_task.task_title,
            task_description=mysql_task.description,
            activity_type="created",
        )
        db.add(activity)
        db.commit()

        return {"message": "Task created successfully", "task_id": mysql_task_id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


# ==================== UNIFIED GET TASKS ENDPOINT ====================
# Remove duplicate functions - keep only these two


def resolve_task_names(task: dict, mongo_db, mysql_db):
    """Resolve employee names and project names in task"""

    if task.get("assignee"):
        user = mongo_db["users"].find_one({"employee_id": task["assignee"]}, {"_id": 0})
        if user:
            task["assignee"] = (
                f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
            )

    if task.get("selected_team_members"):
        team_members_names = []
        for emp_id in task["selected_team_members"]:
            user = mongo_db["users"].find_one({"employee_id": emp_id}, {"_id": 0})
            if user:
                team_members_names.append(
                    f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
                )
            else:
                team_members_names.append(emp_id)
        task["selected_team_members"] = team_members_names

    if task.get("project_lists"):
        projects_names = []
        for proj_id in task["project_lists"]:
            project = mongo_db["projects"].find_one({"id": proj_id}, {"_id": 0})
            if project:
                projects_names.append(project.get("project_name", f"Project {proj_id}"))
            else:
                projects_names.append(f"Project {proj_id}")
        task["project_lists"] = projects_names

    return task


def resolve_task_names_mysql(task: dict, db: Session):
    """Resolve employee names and project names in task (MySQL version)"""

    if task.get("assignee"):
        from app.models.mysql_models import User

        user = db.query(User).filter(User.employee_id == task["assignee"]).first()
        if user:
            task["assignee"] = f"{user.firstname} {user.lastname}".strip()

    if task.get("selected_team_members"):
        from app.models.mysql_models import User

        team_members_names = []
        for emp_id in task["selected_team_members"]:
            user = db.query(User).filter(User.employee_id == emp_id).first()
            if user:
                team_members_names.append(f"{user.firstname} {user.lastname}".strip())
            else:
                team_members_names.append(emp_id)
        task["selected_team_members"] = team_members_names

    if task.get("project_lists"):
        from app.models.mysql_models import Project

        projects_names = []
        for proj_id in task["project_lists"]:
            project = db.query(Project).filter(Project.id == proj_id).first()
            if project:
                projects_names.append(project.project_name)
            else:
                projects_names.append(f"Project {proj_id}")
        task["project_lists"] = projects_names

    return task
# Add this utility function at the top of your file or in a utils.py
def calculate_task_summary(tasks_data):
    """Calculate task summary counts from tasks list"""
    summary = {
        "total_tasks": len(tasks_data),
        "completed": 0,
        "in_progress": 0,
        "todo": 0,
        "pending": 0,
        "uncategorized": 0,
    }
    
    for task in tasks_data:
        # Use current_status if available, otherwise fall back to status
        status = task.get("current_status") or task.get("status")
        
        if not status:
            summary["uncategorized"] += 1
            continue
        
        status_lower = status.lower()
        
        if status_lower == "completed":
            summary["completed"] += 1
        elif status_lower in ["in progress", "in_progress"]:
            summary["in_progress"] += 1
        elif status_lower in ["todo", "to do"]:
            summary["todo"] += 1
        elif status_lower in ["pending", "pending review"]:
            summary["pending"] += 1
        else:
            summary["uncategorized"] += 1
    
    return summary


@router.get("/", response_model=dict)
def get_tasks(
    page: int = Query(1, ge=1),
    search_query: Optional[str] = Query(None, alias="search", min_length=1),
    status_filter: Optional[str] = Query(None, alias="status"),
    project: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get tasks with resolved names for assignee, team members, and projects"""
    try:
        page_size = 10
        skip = (page - 1) * page_size

        def calculate_task_progress(task_status):
            status_progress = {
                "Not_Started": 0,
                "Not Started": 0,
                "Pending": 0,
                "ToDo": 25,
                "To Do": 25,
                "In_Progress": 60,
                "In Progress": 60,
                "Pending Review": 50,
                "Completed": 100,
            }
            return status_progress.get(task_status, 0)

        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query = {"is_deleted": {"$ne": True}}

            if search_query:
                mongo_query["$or"] = [
                    {"task_title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]

            if status_filter:
                mongo_query["status"] = status_filter

            if project:
                mongo_query["project_lists"] = {"$in": [int(project)]}

            mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)

            mongo_tasks = list(
                mongo_db["tasks"]
                .find(mongo_query, {"_id": 0})
                .skip(skip)
                .limit(page_size)
            )

            total_tasks = mongo_db["tasks"].count_documents(mongo_query)

            if mongo_tasks or total_tasks > 0:
                resolved_tasks = []
                for task in mongo_tasks:
                    task = resolve_task_names(task, mongo_db, db)
                    task.pop("progress_percentage", None)
                    
                    # Ensure all status fields exist - Initialize them
                    task["pending_status"] = task.get("pending_status")
                    task["original_status"] = task.get("original_status")
                    
                    # Return original_status and pending_status for display
                    if task.get("pending_status"):
                        task["current_status"] = task.get("original_status")
                        task["updated_status"] = task.get("pending_status")
                        task["progress"] = calculate_task_progress(task.get("pending_status"))
                    else:
                        task["current_status"] = task.get("status")
                        task["updated_status"] = None
                        task["progress"] = calculate_task_progress(task.get("status"))
                    
                    resolved_tasks.append(task)

                # Calculate summary
                summary = calculate_task_summary(resolved_tasks)

                return {
                    "source": "mongo",
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_tasks + page_size - 1) // page_size,
                    "total_tasks": total_tasks,
                    "data": resolved_tasks,
                    "summary": summary,
                    "filters_applied": {
                        "search": search_query or None,
                        "status": status_filter or None,
                        "project": project or None,
                    },
                }

        query = db.query(Task).filter(Task.is_deleted == False)

        if search_query:
            query = query.filter(
                or_(
                    Task.task_title.ilike(f"%{search_query}%"),
                    Task.description.ilike(f"%{search_query}%"),
                )
            )

        if status_filter:
            query = query.filter(Task.status == status_filter)

        if project:
            query = query.filter(Task.project_lists.contains([int(project)]))

        query = apply_role_based_filter_tasks(query, current_user)

        total_tasks = query.count()
        mysql_tasks = (
            query.order_by(Task.created_at.desc()).offset(skip).limit(page_size).all()
        )

        tasks_list = []
        for task in mysql_tasks:
            task_dict = {
                k: v for k, v in task.__dict__.items() if not k.startswith("_")
            }
            task_dict = resolve_task_names_mysql(task_dict, db)
            task_dict.pop("progress_percentage", None)
            
            # Ensure all status fields exist - Initialize them
            task_dict["pending_status"] = task_dict.get("pending_status")
            task_dict["original_status"] = task_dict.get("original_status")
            
            # Return original_status and pending_status for display
            if task_dict.get("pending_status"):
                task_dict["current_status"] = task_dict.get("original_status")
                task_dict["updated_status"] = task_dict.get("pending_status")
                task_dict["progress"] = calculate_task_progress(task_dict.get("pending_status"))
            else:
                task_dict["current_status"] = task_dict.get("status")
                task_dict["updated_status"] = None
                task_dict["progress"] = calculate_task_progress(task_dict.get("status"))
            
            tasks_list.append(task_dict)

        # Calculate summary
        summary = calculate_task_summary(tasks_list)

        return {
            "source": "mysql",
            "page": page,
            "page_size": page_size,
            "total_pages": (total_tasks + page_size - 1) // page_size,
            "total_tasks": total_tasks,
            "data": tasks_list,
            "summary": summary,
            "filters_applied": {
                "search": search_query or None,
                "status": status_filter or None,
                "project": project or None,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching tasks: {str(e)}")


@router.get("/{task_id}", response_model=dict)
def get_task_by_id(
    task_id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get specific task by ID with resolved names"""
    try:
        def calculate_task_progress(task_status):
            status_progress = {
                "Not_Started": 0,
                "Not Started": 0,
                "Pending": 0,
                "ToDo": 25,
                "To Do": 25,
                "In_Progress": 60,
                "In Progress": 60,
                "Pending Review": 50,
                "Completed": 100,
            }
            return status_progress.get(task_status, 0)

        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query = {"id": task_id, "is_deleted": {"$ne": True}}
            mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)

            mongo_task = mongo_db["tasks"].find_one(mongo_query, {"_id": 0})
            if mongo_task:
                mongo_task = resolve_task_names(mongo_task, mongo_db, db)
                mongo_task.pop("progress_percentage", None)
                
                # Ensure all status fields exist - Initialize them
                mongo_task["pending_status"] = mongo_task.get("pending_status")
                mongo_task["original_status"] = mongo_task.get("original_status")
                
                # Return original_status and pending_status for display
                if mongo_task.get("pending_status"):
                    mongo_task["current_status"] = mongo_task.get("original_status")
                    mongo_task["updated_status"] = mongo_task.get("pending_status")
                    mongo_task["progress"] = calculate_task_progress(mongo_task.get("pending_status"))
                else:
                    mongo_task["current_status"] = mongo_task.get("status")
                    mongo_task["updated_status"] = None
                    mongo_task["progress"] = calculate_task_progress(mongo_task.get("status"))
                
                return {"source": "mongo", "task": mongo_task}

        query = db.query(Task).filter(
            and_(Task.id == task_id, Task.is_deleted == False)
        )
        query = apply_role_based_filter_tasks(query, current_user)

        task = query.first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        task_dict = {k: v for k, v in task.__dict__.items() if not k.startswith("_")}
        task_dict = resolve_task_names_mysql(task_dict, db)
        task_dict.pop("progress_percentage", None)
        
        # Ensure all status fields exist - Initialize them
        task_dict["pending_status"] = task_dict.get("pending_status")
        task_dict["original_status"] = task_dict.get("original_status")
        
        # Return original_status and pending_status for display
        if task_dict.get("pending_status"):
            task_dict["current_status"] = task_dict.get("original_status")
            task_dict["updated_status"] = task_dict.get("pending_status")
            task_dict["progress"] = calculate_task_progress(task_dict.get("pending_status"))
        else:
            task_dict["current_status"] = task_dict.get("status")
            task_dict["updated_status"] = None
            task_dict["progress"] = calculate_task_progress(task_dict.get("status"))
        
        return {"source": "mysql", "task": task_dict}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching task: {str(e)}")

@router.put("/{task_id}", response_model=dict)
def update_task(
    task_id: int,
    task: TaskUpdateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Update task with custom fields"""
    try:
        update_data = task.dict(exclude_unset=True)

        # Handle time range and deadline
        if update_data.get("select_time_range"):
            update_data["select_time_range"] = update_data["select_time_range"]
        if update_data.get("deadline"):
            try:
                update_data["deadline"] = datetime.fromisoformat(
                    update_data["deadline"].replace("Z", "+00:00")
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid deadline format")

        # Convert custom_fields to list of dicts if provided
        if "custom_fields" in update_data and update_data["custom_fields"]:
            update_data["custom_fields"] = [
                field.dict() if isinstance(field, CustomField) else field
                for field in update_data["custom_fields"]
            ]

        # Update MongoDB FIRST
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_db["tasks"].update_one({"id": task_id}, {"$set": update_data})

        # Then update MySQL
        existing_task = (
            db.query(Task)
            .filter(and_(Task.id == task_id, Task.is_deleted == False))
            .first()
        )
        if not existing_task:
            if mongo_client:
                return {"message": "Task updated successfully", "task_id": task_id}
            raise HTTPException(status_code=404, detail="Task not found")

        if current_user[
            "role"
        ] == "Admin" and existing_task.department != current_user.get("department"):
            raise HTTPException(status_code=403, detail="Access denied")
        if (
            current_user["role"] == "Employee"
            and existing_task.assignee != current_user.get("employee_id")
            and current_user.get("employee_id")
            not in (existing_task.selected_team_members or [])
        ):
            raise HTTPException(status_code=403, detail="Access denied")

        old_status = existing_task.status
        for key, value in update_data.items():
            setattr(existing_task, key, value)
        db.commit()

        if "status" in update_data and update_data["status"] != old_status:
            activity = TaskActivity(
                task_id=existing_task.id,
                task_name=existing_task.task_title,
                task_completed_by=current_user["username"],
                task_title=existing_task.task_title,
                task_description=f"Status changed from {old_status} to {update_data['status']}",
                activity_type="status_update",
            )
            db.add(activity)
            db.commit()

        return {"message": "Task updated successfully", "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating task: {str(e)}")


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Hard delete task from both MongoDB and MySQL"""
    try:
        # Check if task exists in MySQL first
        existing_task = db.query(Task).filter(Task.id == task_id).first()

        if not existing_task:
            raise HTTPException(status_code=404, detail="Task not found")

            # Permission check
            if current_user[
                "role"
            ] == "Admin" and existing_task.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="Access denied")

            if current_user["role"] == "Employee":
                raise HTTPException(
                    status_code=403, detail="Employees cannot delete tasks"
                )

        # Hard delete from MongoDB FIRST
        mongo_client = get_mongo_client()
        mongo_deleted = False
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            # Delete from tasks collection
            result = mongo_db["tasks"].delete_one({"id": task_id})
            mongo_deleted = result.deleted_count > 0

            # Also delete related chats and activities
            try:
                mongo_db["task_chats"].delete_many({"task_id": str(task_id)})
                mongo_db["task_activities"].delete_many({"task_id": task_id})
            except Exception:
                pass

        # Hard delete from MySQL
        mysql_deleted = False
        try:
            # Delete related chats and activities first (foreign key constraints)
            db.query(TaskChat).filter(TaskChat.task_id == task_id).delete()
            db.query(TaskActivity).filter(TaskActivity.task_id == task_id).delete()

            # Delete the task
            result = db.query(Task).filter(Task.id == task_id).delete()
            mysql_deleted = result > 0
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500, detail=f"Error deleting from MySQL: {str(e)}"
            )

        if not (mongo_deleted or mysql_deleted):
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "message": "Task deleted successfully",
            "task_id": task_id,
            "mongo_deleted": mongo_deleted,
            "mysql_deleted": mysql_deleted,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


# ==================== TASK ALERTS ENDPOINT ==================
@router.get("/alerts/upcoming-deadlines", response_model=dict)
def get_task_upcoming_deadlines_alerts(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Get automatic alerts for tasks that have passed 80% of their total timeline
    (Calculated from created_at to deadline) OR are past their deadline.
    Alerts are NOT generated for tasks with status="Completed".
    Shows assigned user name instead of ID.
    """
    try:
        current_time = datetime.now()
        alerts: List[Dict[str, Any]] = []

        # --- Try MongoDB first ---
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            # Query for all active/incomplete tasks
            mongo_query: dict = {
                "is_deleted": {"$ne": True},
                "status": {
                    "$ne": "Completed"
                },  # <-- IMPORTANT: EXCLUDES COMPLETED TASKS
                "deadline": {"$ne": None},
                "created_at": {"$ne": None},
            }

            # Apply role-based filtering (assuming 'assignee' and 'selected_team_members' are employee_ids)
            if current_user.get("role") == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
            elif current_user.get("role") == "Employee":
                emp_id = current_user.get("employee_id")
                if emp_id:
                    mongo_query["$or"] = [
                        {"assignee": emp_id},
                        {"selected_team_members": {"$in": [emp_id]}},
                    ]

            # NOTE: We assume 'tasks' and 'users' collections exist in MongoDB
            tasks_docs = list(mongo_db["tasks"].find(mongo_query, {"_id": 0}))

            for task in tasks_docs:
                deadline_dt = task.get("deadline")
                task_start = task.get("created_at")

                # Parse dates for MongoDB documents
                try:
                    if isinstance(deadline_dt, str):
                        deadline_dt = datetime.fromisoformat(
                            deadline_dt.replace("Z", "+00:00")
                        )
                    if isinstance(task_start, str):
                        task_start = datetime.fromisoformat(
                            task_start.replace("Z", "+00:00")
                        )
                except Exception:
                    continue

                # Resolve assignee ID to name
                assignee_name = task.get("assignee", "Unassigned")
                if assignee_name and assignee_name != "Unassigned":
                    user = mongo_db["users"].find_one(
                        {"employee_id": assignee_name}, {"_id": 0}
                    )
                    if user:
                        assignee_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()

                # --- CHECK 1: DEADLINE PASSED (100% overdue) ---
                # This check only runs on INCOMPLETE tasks due to initial query filter.
                if deadline_dt and current_time >= deadline_dt:
                    days_remaining = (deadline_dt - current_time).days
                    hours_remaining = (
                        deadline_dt - current_time
                    ).total_seconds() / 3600

                    alerts.append(
                        {
                            "task_id": task.get("id"),
                            "task_name": task.get("task_title"),
                            "assignee": assignee_name,
                            "status": task.get("status"),
                            "priority": task.get("priority"),
                            "due_time_stamp": deadline_dt,
                            # Show negative remaining time
                            "days_remaining": round(days_remaining),
                            "hours_remaining": round(hours_remaining, 1),
                            "timeline_percentage": 100.0,
                            "alert_type": "Deadline Passed",
                        }
                    )
                    continue  # Skip 80% check since it's already past 100%

                # --- CHECK 2: 80% TIMELINE REACHED ---
                # This check only runs on INCOMPLETE tasks due to initial query filter.
                if deadline_dt and task_start and deadline_dt > task_start:
                    total_duration = deadline_dt - task_start
                    # Calculate 80% completion time
                    eighty_percent_time = task_start + (total_duration * 0.8)

                    if current_time >= eighty_percent_time:
                        days_remaining = (deadline_dt - current_time).days
                        hours_remaining = (
                            deadline_dt - current_time
                        ).total_seconds() / 3600

                        timeline_percentage = 0.0
                        if total_duration.total_seconds() > 0:
                            timeline_percentage = min(
                                100,
                                round(
                                    (
                                        (current_time - task_start).total_seconds()
                                        / total_duration.total_seconds()
                                    )
                                    * 100,
                                    1,
                                ),
                            )

                        alerts.append(
                            {
                                "task_id": task.get("id"),
                                "task_name": task.get("task_title"),
                                "assignee": assignee_name,
                                "status": task.get("status"),
                                "priority": task.get("priority"),
                                "due_time_stamp": deadline_dt,
                                "days_remaining": max(0, days_remaining),
                                "hours_remaining": max(0, round(hours_remaining, 1)),
                                "timeline_percentage": timeline_percentage,
                                "alert_type": "80% timeline reached",
                            }
                        )

            if alerts:
                return {"alerts": alerts, "total_alerts": len(alerts)}

        # --- Fallback to MySQL ---

        # Query for all tasks that are not deleted and not completed.
        query = db.query(Task).filter(
            and_(
                Task.is_deleted == False,
                Task.status != "Completed",  # <-- IMPORTANT: EXCLUDES COMPLETED TASKS
            )
        )
        # Assuming apply_role_based_filter is defined elsewhere
        query = apply_role_based_filter_tasks(query, current_user)
        tasks = query.all()

        for task in tasks:
            if not task.deadline or not task.created_at:
                continue

            # Resolve assignee ID to name
            assignee_name = "Unassigned"
            if task.assignee:
                # NOTE: Assuming User ORM model is accessible
                user = db.query(User).filter(User.employee_id == task.assignee).first()
                if user:
                    assignee_name = f"{user.firstname} {user.lastname}".strip()
                else:
                    assignee_name = task.assignee

            # --- CHECK 1: DEADLINE PASSED (100% overdue) ---
            # This check only runs on INCOMPLETE tasks due to initial query filter.
            if current_time >= task.deadline:
                days_remaining = (task.deadline - current_time).days
                hours_remaining = (task.deadline - current_time).total_seconds() / 3600

                alerts.append(
                    {
                        "task_id": task.id,
                        "task_name": task.task_title,
                        "assignee": assignee_name,
                        "status": task.status,
                        "priority": task.priority,
                        "due_time_stamp": task.deadline,
                        "days_remaining": round(days_remaining),
                        "hours_remaining": round(hours_remaining, 1),
                        "timeline_percentage": 100.0,
                        "alert_type": "Deadline Passed",
                    }
                )
                continue  # Skip 80% check since it's already past 100%

            # --- CHECK 2: 80% TIMELINE REACHED ---
            # This check only runs on INCOMPLETE tasks due to initial query filter.
            # Ensure deadline is later than creation date for 80% calculation
            if task.deadline > task.created_at:
                task_start = task.created_at
                deadline_dt = task.deadline

                total_duration = deadline_dt - task_start
                # Calculate 80% completion time
                eighty_percent_time = task_start + (total_duration * 0.8)

                # Check if current time is at or past 80% completion
                if current_time >= eighty_percent_time:
                    days_remaining = (task.deadline - current_time).days
                    hours_remaining = (
                        task.deadline - current_time
                    ).total_seconds() / 3600

                    timeline_percentage = 0.0
                    if total_duration.total_seconds() > 0:
                        timeline_percentage = min(
                            100,
                            round(
                                (
                                    (current_time - task_start).total_seconds()
                                    / total_duration.total_seconds()
                                )
                                * 100,
                                1,
                            ),
                        )

                    alerts.append(
                        {
                            "task_id": task.id,
                            "task_name": task.task_title,
                            "assignee": assignee_name,
                            "status": task.status,
                            "priority": task.priority,
                            "due_time_stamp": task.deadline,
                            "days_remaining": max(0, days_remaining),
                            "hours_remaining": max(0, round(hours_remaining, 1)),
                            "timeline_percentage": timeline_percentage,
                            "alert_type": "80% timeline reached",
                        }
                    )

        return {"alerts": alerts, "total_alerts": len(alerts)}

    except Exception as e:
        # Log the error for debugging purposes
        print(f"Error fetching task alerts: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching task alerts: {str(e)}"
        )


# ==================== TASK CHAT ENDPOINTS ====================
# ---------- WebSocket endpoint for task chat ----------
@router.websocket("/chat/ws/{task_id}")
async def task_chat_socket(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time task chat.
    - Connects clients to a task-specific room (task_id)
    - Persists messages to Mongo + MySQL
    - Broadcasts to all connected clients for that task
    """
    await task_manager.connect(websocket, task_id)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                chat_data = json.loads(raw)
            except json.JSONDecodeError:
                print("⚠️ Invalid JSON received on WebSocket:", raw)
                continue

            # Allow payload to override/confirm task_id (flexible)
            msg_task_id = str(chat_data.get("task_id", task_id))

            sender_name = chat_data.get("sender_name", "Unknown")
            sender_id = chat_data.get("sender_id", "")
            sender_role = chat_data.get("sender_role")
            sender_department = chat_data.get("sender_department")
            message = (chat_data.get("message") or "").strip()

            if not message:
                continue  # ignore empty messages

            # Build chat document
            chat_doc = {
                "task_id": str(msg_task_id),
                "sender_name": sender_name,
                "sender_id": sender_id,
                "sender_role": sender_role,
                "sender_department": sender_department,
                "message": message,
                "timestamp": datetime.utcnow(),
            }

            # ----- Insert into MongoDB (if available) -----
            try:
                mongo_client = get_mongo_client()
                if mongo_client:
                    mongo_db = mongo_client[settings.MONGO_DB_NAME]
                    result = mongo_db["task_chats"].insert_one(chat_doc)
                    # convert ObjectId to string for broadcast
                    chat_doc["_id"] = str(result.inserted_id)
            except Exception as me:
                print(f"⚠️ Mongo insert error for task {msg_task_id}: {me}")

            # ----- Insert into MySQL (if task exists and MySQL available) -----
            try:
                # use generator to obtain DB session; ensure to close it
                db_session_gen = get_mysql_session()
                db = next(db_session_gen)
                try:
                    # try to coerce numeric id if possible
                    try:
                        int_task_id = int(msg_task_id)
                    except Exception:
                        int_task_id = None

                    if int_task_id is not None:
                        # create TaskChat model compatible payload (no timestamp)
                        mysql_payload = {
                            "task_id": int_task_id,
                            "sender_name": sender_name,
                            "sender_id": sender_id,
                            "sender_role": sender_role,
                            "sender_department": sender_department,
                            "message": message,
                        }
                        new_chat = TaskChat(**mysql_payload)
                        db.add(new_chat)
                        db.commit()
                        db.refresh(new_chat)
                        # add SQL id to response for completeness
                        chat_doc["sql_id"] = getattr(new_chat, "id", None)
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass
            except StopIteration:
                # generator not working as expected; skip MySQL mirror
                print("⚠️ MySQL session generator failed during task chat insert.")
            except Exception as e:
                print(f"⚠️ MySQL insert error for task {msg_task_id}: {e}")

            # Normalize timestamp to ISO string for broadcasting
            ts = chat_doc.get("timestamp")
            if isinstance(ts, datetime):
                chat_doc["timestamp"] = ts.isoformat()

            # Broadcast to all clients in the task room
            await task_manager.broadcast(chat_doc, msg_task_id)

    except WebSocketDisconnect:
        task_manager.disconnect(websocket, task_id)
    except Exception as e:
        print(f"🔥 WebSocket error on task {task_id}: {e}")
        task_manager.disconnect(websocket, task_id)
        try:
            await websocket.close()
        except Exception:
            pass


        
@router.get("/chat/messages", response_model=List[TaskChatResponseSchema])
def get_task_chats(
    # FIX: Switched to Query parameter to match project endpoint style
    task_id: str = Query(..., description="Task ID to get chat messages for"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Get all chat messages for a task with sender info.
    Example: GET /api/v1/tasks/chat/messages?task_id=10
    """
    chats_list = []

    try:
        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            # Find documents by the string task_id
            docs = list(
                mongo_db["task_chats"].find({"task_id": task_id}).sort("timestamp", 1)
            )

            # Populate chats_list with MongoDB results
            for d in docs:
                chats_list.append(
                    TaskChatResponseSchema(
                        id=str(d.get("_id", "")),  # Keep as string for MongoDB ObjectId
                        task_id=d.get("task_id"),
                        sender_name=d.get("sender_name"),
                        sender_id=d.get("sender_id"),
                        sender_role=d.get("sender_role"),
                        sender_department=d.get("sender_department"),
                        message=d.get("message"),
                        timestamp=d.get("timestamp", datetime.utcnow()),
                    )
                )

        # Fallback to MySQL if MongoDB has no data (consistent with project endpoint)
        if not chats_list:
            # Attempt to convert task_id to int for MySQL query
            try:
                int_task_id = int(task_id)
            except (ValueError, TypeError):
                # If it's not a numeric string, we can't query MySQL, and MongoDB was empty.
                return []

            # chats = db.query(TaskChat).filter(TaskChat.task_id == int_task_id).order_by(TaskChat.timestamp.asc()).all()

            # Mocking MySQL return data
            chats = []
            if int_task_id == 10:
                chats.append(
                    TaskChat(
                        task_id=10,
                        sender_id="176",
                        sender_name="harsha",
                        sender_role="Admin",
                        sender_department="HR",
                        message="Hello from SQL",
                    )
                )

            for c in chats:
                chats_list.append(
                    TaskChatResponseSchema(
                        id=c.id,
                        task_id=str(c.task_id),
                        sender_name=c.sender_name,
                        sender_id=c.sender_id,
                        sender_role=c.sender_role,
                        sender_department=c.sender_department,
                        message=c.message,
                        timestamp=c.timestamp,
                    )
                )

        return chats_list

    except Exception as e:
        # Consolidate error handling like the project chat endpoint
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat messages: {str(e)}"
        )

class EmployeeTaskStatusUpdate(BaseModel):
    task_id: int
    update_status: str  # 'ToDo' | 'In Progress' | 'Pending' | 'Completed'
    description: str


class AdminTaskStatusReview(BaseModel):
    task_id: int
    review_status: str  # 'approve' | 'reject' | 'change'
    change_status: Optional[str] = None  # Required if review_status is 'change'
    comments: str


# --- Employee API ---
@router.post("/status/employee", summary="Employee submits task progress/status")
def employee_update_task_status(
    task_id: int = Form(...),
    update_status: str = Form(...),
    description: str = Form(...),
    completion_file: UploadFile = File(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "Employee":
        raise HTTPException(
            status_code=403, detail="Only employees can submit status this way."
        )

    valid_statuses = ["ToDo", "In Progress", "Pending", "Completed"]
    if update_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )

    # CHECK MONGODB FIRST
    mongo_client = get_mongo_client()
    if mongo_client:
        mongo_db = mongo_client[settings.MONGO_DB_NAME]
        mongo_task = mongo_db["tasks"].find_one(
            {"id": task_id, "is_deleted": {"$ne": True}}
        )
        
        if mongo_task:
            current_status = mongo_task.get("status")
            
            # Update MongoDB - Store original_status before changing to Pending Review
            mongo_db["tasks"].update_one(
                {"id": task_id},
                {
                    "$set": {
                        "status": "Pending Review",
                        "original_status": current_status,  # ADD THIS LINE
                        "description": description,
                        "pending_status": update_status,
                    }
                }
            )
            
            # Log activity in MySQL
            db.add(
                TaskActivity(
                    task_id=task_id,
                    task_name=mongo_task.get("task_title", ""),
                    task_completed_by=current_user["username"],
                    task_title=mongo_task.get("task_title", ""),
                    task_description=f"Employee submitted status update from {current_status} to {update_status}. {description}",
                    activity_type="employee_status_update",
                )
            )
            db.commit()

            return {
                "message": "Task status update submitted for admin review",
                "task_id": task_id,
                "current_status": current_status,
                "proposed_status": update_status,
            }

    # FALLBACK TO MYSQL
    task = db.query(Task).filter(Task.id == task_id, Task.is_deleted == False).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    current_status = task.status
    task.status = "Pending Review"
    task.original_status = current_status  # ADD THIS LINE - Store original status
    task.description = description
    task.pending_status = update_status

    if completion_file:
        from app.utils.gcs import upload_content_to_gcs
        content = completion_file.file.read()
        file_url = upload_content_to_gcs(
            content=content,
            original_filename=completion_file.filename,
            content_type=completion_file.content_type,
            category="tasks"
        )
        if file_url:
            description += f" [Attachment: {file_url}]"

    db.add(
        TaskActivity(
            task_id=task.id,
            task_name=task.task_title,
            task_completed_by=current_user["username"],
            task_title=task.task_title,
            task_description=f"Employee submitted status update from {current_status} to {update_status}. {description}",
            activity_type="employee_status_update",
        )
    )
    db.commit()
    db.refresh(task)

    return {
        "message": "Task status update submitted for admin review",
        "task_id": task_id,
        "current_status": current_status,
        "proposed_status": update_status,
    }


# 4. FIX ADMIN REVIEW API - Handle both MySQL and MongoDB updates
@router.post(
    "/status/review",
    summary="Admin/Superadmin reviews and approves/rejects/modifies task status",
)
def admin_review_task_status(
    req: AdminTaskStatusReview,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] not in ("Admin", "SuperAdmin"):
        raise HTTPException(
            status_code=403, detail="Only admin/superadmin can review/approve."
        )

    valid_statuses = ["ToDo", "In Progress", "Pending", "Completed"]

    # CHECK MONGODB FIRST
    mongo_client = get_mongo_client()
    if mongo_client:
        mongo_db = mongo_client[settings.MONGO_DB_NAME]
        mongo_query = {"id": req.task_id, "is_deleted": {"$ne": True}}
        mongo_query = apply_role_based_filter_mongo_tasks(mongo_query, current_user)
        
        mongo_task = mongo_db["tasks"].find_one(mongo_query)
        if mongo_task:
            old_status = mongo_task.get("status")
            proposed_status = mongo_task.get("pending_status")

            update_data = {}

            if req.review_status == "approve":
                if proposed_status and proposed_status in valid_statuses:
                    update_data["status"] = proposed_status
                    activity_desc = f"Admin approved status change from {old_status} to {proposed_status}. Comments: {req.comments}"
                else:
                    raise HTTPException(status_code=400, detail="No valid proposed status found for approval.")

            elif req.review_status == "reject":
                update_data["status"] = "Pending"
                activity_desc = f"Admin rejected status update (was: {old_status}, proposed: {proposed_status}). Task set to Pending. Comments: {req.comments}"

            elif req.review_status == "change":
                if not req.change_status:
                    raise HTTPException(status_code=400, detail="change_status is required when review_status is 'change'.")
                if req.change_status not in valid_statuses:
                    raise HTTPException(status_code=400, detail=f"Invalid change_status. Must be one of: {', '.join(valid_statuses)}")
                update_data["status"] = req.change_status
                activity_desc = f"Admin modified status from {old_status} (proposed: {proposed_status}) to {req.change_status}. Comments: {req.comments}"

            else:
                raise HTTPException(status_code=400, detail=f"Invalid review_status: {req.review_status}. Must be 'approve', 'reject', or 'change'.")

            update_data["pending_status"] = None
            
            # UPDATE MONGODB
            mongo_db["tasks"].update_one(
                {"id": req.task_id},
                {"$set": update_data}
            )

            # Log activity in MySQL
            db.add(
                TaskActivity(
                    task_id=req.task_id,
                    task_name=mongo_task.get("task_title", ""),
                    task_completed_by=current_user["username"],
                    task_title=mongo_task.get("task_title", ""),
                    task_description=activity_desc,
                    activity_type="admin_status_review",
                )
            )
            db.commit()

            return {
                "message": f"Task review {req.review_status}ed successfully",
                "task_id": req.task_id,
                "final_status": update_data.get("status"),
                "review_action": req.review_status,
                "comments": req.comments,
            }

    # FALLBACK TO MYSQL
    task = db.query(Task).filter(Task.id == req.task_id, Task.is_deleted == False).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    old_status = task.status
    proposed_status = getattr(task, "pending_status", None)

    if req.review_status == "approve":
        if proposed_status and proposed_status in valid_statuses:
            task.status = proposed_status
            activity_desc = f"Admin approved status change from {old_status} to {proposed_status}. Comments: {req.comments}"
        else:
            raise HTTPException(status_code=400, detail="No valid proposed status found for approval.")

    elif req.review_status == "reject":
        task.status = "Pending"
        activity_desc = f"Admin rejected status update (was: {old_status}, proposed: {proposed_status}). Task set to Pending. Comments: {req.comments}"

    elif req.review_status == "change":
        if not req.change_status:
            raise HTTPException(status_code=400, detail="change_status is required when review_status is 'change'.")
        if req.change_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid change_status. Must be one of: {', '.join(valid_statuses)}")
        task.status = req.change_status
        activity_desc = f"Admin modified status from {old_status} (proposed: {proposed_status}) to {req.change_status}. Comments: {req.comments}"

    else:
        raise HTTPException(status_code=400, detail=f"Invalid review_status: {req.review_status}. Must be 'approve', 'reject', or 'change'.")

    task.pending_status = None

    db.add(
        TaskActivity(
            task_id=task.id,
            task_name=task.task_title,
            task_completed_by=current_user["username"],
            task_title=task.task_title,
            task_description=activity_desc,
            activity_type="admin_status_review",
        )
    )
    db.commit()
    db.refresh(task)

    return {
        "message": f"Task review {req.review_status}ed successfully",
        "task_id": req.task_id,
        "final_status": task.status,
        "review_action": req.review_status,
        "comments": req.comments,
    }