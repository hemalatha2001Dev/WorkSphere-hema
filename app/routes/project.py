from datetime import datetime, timedelta,timezone
from typing import List, Optional,Dict
import json
import logging
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status,WebSocket, WebSocketDisconnect
from pymongo import ReturnDocument
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from app.core.util import get_alert_count  
from app.core.config import settings
from app.core.filters import (
    apply_role_based_filter_mongo_projects,
    apply_role_based_filter_projects,
)
from app.core.security import get_current_user
from app.db.mongo import get_mongo_client
from app.db.mysql import get_mysql_session
from app.models.mysql_models import Project, ProjectChat, ProjectFile, Task
from app.schemas.user_schema import (
    AlertSchema,
    ChatMessageSchema,
    ChatResponseSchema,
    DashboardMetricsSchema,
    MonthlyProgressSchema,
    ProjectCreateSchema,
    ProjectResponseSchema,
    ProjectsResponse,
    ProjectStatusDistributionSchema,
    ProjectUpdateSchema,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# Helper function for role-based access
def apply_role_based_filter(query, current_user: dict):
    """Apply role-based filtering to project queries"""
    role = current_user["role"]
    department = current_user.get("department")

    if role == "Admin" and department:
        # Admin can only see projects from their department
        query = query.filter(Project.department == department)
    elif role == "Employee":
        # Employee can only see projects they're assigned to
        username = current_user["username"]
        query = query.filter(
            or_(
                Project.project_admins.contains([username]),
                Project.selected_team_members.contains([username]),
            )
        )
    # SuperAdmin can see all projects (no filter needed)

    return query


# Helper function to calculate project progress
def calculate_project_progress(add_project_phase: Optional[List[dict]]) -> float:
    # 1. Check if the input is None or not a list, and return 0 if so.
    if not add_project_phase or not isinstance(add_project_phase, list):
        # Log the unexpected type here if you can, to help with debugging
        return 0.0

    total_phases = len(add_project_phase)
    if total_phases == 0:
        return 0.0

    total_progress = 0
    for phase in add_project_phase:
        # 2. Check if 'phase' is a dictionary before calling .get()
        if not isinstance(phase, dict):
            # Log the unexpected type of the phase object and skip it
            continue

        try:
            # This is where the original error ('str' object has no attribute 'get') occurred
            progress_value = float(phase.get("progress", 0))
        except (ValueError, TypeError):
            # Log the invalid value (e.g., progress was a non-numeric string)
            progress_value = 0

        total_progress += progress_value

    overall_progress = total_progress / total_phases
    return round(overall_progress, 2)


# DASHBOARD ENDPOINTS - UPDATED


# --- Connection Manager ---
class ConnectionManager:
    def __init__(self):
        # Each project_id maps to a list of active WebSocket connections
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, project_id: int):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = []
        self.active_connections[project_id].append(websocket)
        print(f"✅ Client connected for Project {project_id} | Total: {len(self.active_connections[project_id])}")

    def disconnect(self, websocket: WebSocket, project_id: int):
        if project_id in self.active_connections:
            if websocket in self.active_connections[project_id]:
                self.active_connections[project_id].remove(websocket)
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]
        print(f"❌ Client disconnected from Project {project_id}")

    async def broadcast(self, message: dict, project_id: int):
        """Send message to all sockets connected for that project"""
        if project_id in self.active_connections:
            for connection in list(self.active_connections[project_id]):
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"⚠️ Broadcast failed for Project {project_id}: {e}")

manager = ConnectionManager()


@router.get("/dashboard/metrics", response_model=dict)
def get_dashboard_metrics(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: Dict = Depends(get_current_user),
):
    """Get dashboard key metrics with status breakdown and deadline alerts"""
    try:
        # Parse dates if provided
        start_dt = None
        end_dt = None
        if start_date and end_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                logger.debug(f"Date filter applied: {start_dt} to {end_dt}")
            except ValueError as ve:
                logger.error(f"Invalid date format: {ve}")
                raise HTTPException(status_code=400, detail="Invalid date format")

        # Try Mongo first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query: Dict = {"is_deleted": {"$ne": True}}
            if start_dt and end_dt:
                mongo_query["created_at"] = {"$gte": start_dt, "$lte": end_dt}

            # Role-based filter
            if current_user["role"] == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
                logger.debug(f"Admin department filter: {current_user['department']}")
            elif current_user["role"] == "Employee":
                username = current_user["username"]
                mongo_query["$or"] = [
                    {"project_admins": {"$in": [username]}},
                    {"selected_team_members": {"$in": [username]}},
                ]
                logger.debug(f"Employee filter: username={username}")

            total_projects = mongo_db["projects"].count_documents(mongo_query)
            not_started = mongo_db["projects"].count_documents(
                {**mongo_query, "project_status": "Not Started"}
            )
            to_do = mongo_db["projects"].count_documents(
                {**mongo_query, "project_status": "ToDo"}
            )
            in_progress = mongo_db["projects"].count_documents(
                {**mongo_query, "project_status": "In Progress"}
            )
            completed = mongo_db["projects"].count_documents(
                {**mongo_query, "project_status": "Completed"}
            )

            # Get alert count using helper function
            total_alerts = get_alert_count(db, mongo_db, current_user, start_dt, end_dt)

            return {
                "total_projects": total_projects,
                "Not_Started": not_started,
                "ToDo": to_do,
                "In_Progress": in_progress,
                "Completed": completed,
                "total_alerts": total_alerts,
            }

        # Fallback to MySQL
        base_query = db.query(Project).filter(Project.is_deleted == False)
        base_query = apply_role_based_filter_projects(base_query, current_user)
        if start_dt and end_dt:
            base_query = base_query.filter(Project.created_at.between(start_dt, end_dt))

        total_projects = base_query.count()
        not_started = base_query.filter(Project.project_status == "Not Started").count()
        to_do = base_query.filter(Project.project_status == "ToDo").count()
        in_progress = base_query.filter(Project.project_status == "In Progress").count()
        completed = base_query.filter(Project.project_status == "Completed").count()

        # Get alert count using helper function
        total_alerts = get_alert_count(db, None, current_user, start_dt, end_dt)

        return {
            "total_projects": total_projects,
            "Not_Started": not_started,
            "ToDo": to_do,
            "In_Progress": in_progress,
            "Completed": completed,
            "total_alerts": total_alerts,
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard metrics: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching dashboard metrics: {str(e)}"
        )


@router.get("/dashboard/monthly-progress", response_model=List[dict])
def get_monthly_progress(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get monthly progress data with status breakdown"""
    try:
        # Try Mongo first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query: dict = {"is_deleted": {"$ne": True}}
            if current_user["role"] == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
            elif current_user["role"] == "Employee":
                username = current_user["username"]
                mongo_query["$or"] = [
                    {"project_admins": {"$in": [username]}},
                    {"selected_team_members": {"$in": [username]}},
                ]

            current_month = datetime.now().strftime("%Y-%m")
            return [
                {
                    "month": current_month,
                    "Not_Started": mongo_db["projects"].count_documents(
                        {**mongo_query, "project_status": "Not Started"}
                    ),
                    "ToDo": mongo_db["projects"].count_documents(
                        {**mongo_query, "project_status": "ToDo"}
                    ),
                    "In_Progress": mongo_db["projects"].count_documents(
                        {**mongo_query, "project_status": "In Progress"}
                    ),
                    "Completed": mongo_db["projects"].count_documents(
                        {**mongo_query, "project_status": "Completed"}
                    ),
                }
            ]

        # Fallback to MySQL
        base_query = db.query(Project).filter(Project.is_deleted == False)
        base_query = apply_role_based_filter_projects(base_query, current_user)
        current_month = datetime.now().strftime("%Y-%m")

        return [
            {
                "month": current_month,
                "Not_Started": base_query.filter(
                    Project.project_status == "Not Started"
                ).count(),
                "ToDo": base_query.filter(Project.project_status == "ToDo").count(),
                "In_Progress": base_query.filter(
                    Project.project_status == "In Progress"
                ).count(),
                "Completed": base_query.filter(
                    Project.project_status == "Completed"
                ).count(),
            }
        ]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching monthly progress: {str(e)}"
        )


@router.get("/dashboard/status-distribution", response_model=dict)
def get_status_distribution(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get project status distribution"""
    try:
        # Try Mongo first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            mongo_query: dict = {"is_deleted": {"$ne": True}}
            if current_user["role"] == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
            elif current_user["role"] == "Employee":
                username = current_user["username"]
                mongo_query["$or"] = [
                    {"project_admins": {"$in": [username]}},
                    {"selected_team_members": {"$in": [username]}},
                ]

            return {
                "Not_Started": mongo_db["projects"].count_documents(
                    {**mongo_query, "project_status": "Not Started"}
                ),
                "ToDo": mongo_db["projects"].count_documents(
                    {**mongo_query, "project_status": "ToDo"}
                ),
                "In_Progress": mongo_db["projects"].count_documents(
                    {**mongo_query, "project_status": "In Progress"}
                ),
                "Completed": mongo_db["projects"].count_documents(
                    {**mongo_query, "project_status": "Completed"}
                ),
            }

        # Fallback to MySQL
        base_query = db.query(Project).filter(Project.is_deleted == False)
        base_query = apply_role_based_filter_projects(base_query, current_user)

        return {
            "Not_Started": base_query.filter(
                Project.project_status == "Not Started"
            ).count(),
            "ToDo": base_query.filter(Project.project_status == "ToDo").count(),
            "In_Progress": base_query.filter(
                Project.project_status == "In Progress"
            ).count(),
            "Completed": base_query.filter(
                Project.project_status == "Completed"
            ).count(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching status distribution: {str(e)}"
        )

@router.post("/create", response_model=dict)
def create_project(
    project: ProjectCreateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Create new project with department-based user restrictions"""
    try:
        # Validate that the user has permission to create a project
        if current_user["role"] not in ("Admin", "SuperAdmin"):
            raise HTTPException(
                status_code=403, detail="Only Admins or SuperAdmins can create projects"
            )

        base_data = project.dict()
        base_data["created_by"] = current_user["username"]

        # 1. Calculate Overall Project Progress
        project_phases = base_data.get("add_project_phase")
        overall_progress = calculate_project_progress(project_phases)
        base_data["progress"] = overall_progress  # Add progress to the data

        # Keep original deadline string for MySQL, convert only for Mongo
        original_deadline_str = base_data.get("deadline")

        # 2. Validate assigned users (project_admins and selected_team_members)
        user_department = current_user.get("department")
        if current_user["role"] == "Admin" and user_department:
            # Fetch users from the same department
            mongo_client = get_mongo_client()
            valid_usernames = set()

            # Try MongoDB first to fetch users
            if mongo_client:
                mongo_db = mongo_client[settings.MONGO_DB_NAME]
                users = mongo_db["users"].find({"department": user_department, "is_deleted": {"$ne": True}})
                valid_usernames = {user["username"] for user in users}
            else:
                # Fallback to MySQL
                from app.models.mysql_models import User  # Import here to avoid circular imports
                users = db.query(User).filter(
                    and_(User.department == user_department, User.is_deleted == False)
                ).all()
                valid_usernames = {user.username for user in users}

            # Validate project_admins
            project_admins = base_data.get("project_admins", [])
            if project_admins and not all(admin in valid_usernames for admin in project_admins):
                raise HTTPException(
                    status_code=400,
                    detail="One or more project admins are not in your department"
                )

            # Validate selected_team_members
            selected_team_members = base_data.get("selected_team_members", [])
            if selected_team_members and not all(member in valid_usernames for member in selected_team_members):
                raise HTTPException(
                    status_code=400,
                    detail="One or more team members are not in your department"
                )

        # Create in Mongo FIRST with numeric id via counters using a COPY
        mongo_client = get_mongo_client()
        mongo_id_val = None
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            seq = mongo_db["counters"].find_one_and_update(
                {"_id": "project_id"},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            project_id_val = int(seq.get("seq", 1))

            mongo_project = dict(base_data)
            mongo_project["id"] = project_id_val
            # Convert deadline to datetime for Mongo only
            if original_deadline_str:
                try:
                    mongo_project["deadline"] = datetime.fromisoformat(
                        original_deadline_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail="Invalid deadline format"
                    )
            mongo_project.setdefault("created_at", datetime.utcnow())
            mongo_project.setdefault("updated_at", datetime.utcnow())
            mongo_project.setdefault("is_deleted", False)
            mongo_db["projects"].insert_one(mongo_project)
            mongo_id_val = project_id_val

        # Prepare clean data for MySQL (no Mongo _id, deadline stays as string)
        sql_project = dict(base_data)
        sql_project.pop("_id", None)
        sql_project.setdefault("is_deleted", False)

        # Mirror to MySQL (fallback store)
        db_project = Project(**sql_project)
        db.add(db_project)
        db.commit()
        db.refresh(db_project)

        return {
            "message": "Project created successfully",
            "project_id": mongo_id_val or db_project.id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating project: {str(e)}")
    
# UNIFIED GET PROJECTS ENDPOINT - Replaces /search, /status/{status}, and default /
@router.get("/", response_model=dict)
def get_projects(
    page: int = Query(1, ge=1),
    search_query: Optional[str] = Query(None, alias="search", min_length=1),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """ """
    try:
        page_size = 10
        skip = (page - 1) * page_size

        def calculate_progress(project_status):
            """Map project status to progress percentage"""
            status_progress = {
                "Not_Started": 0,
                "Not Started": 0,
                "ToDo": 25,
                "To Do": 25,
                "In_Progress": 75,
                "In Progress": 75,
                "Completed": 100,
            }
            return status_progress.get(project_status, 0)

        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            # Build MongoDB query
            mongo_query: dict = {"is_deleted": {"$ne": True}}

            # Apply search filter if provided
            if search_query:
                mongo_query["$or"] = [
                    {"project_name": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                    {"department": {"$regex": search_query, "$options": "i"}},
                ]

            # Apply status filter if provided
            if status:
                mongo_query["project_status"] = status

            # Apply role-based access control using centralized filter
            mongo_query = apply_role_based_filter_mongo_projects(
                mongo_query, current_user
            )

            # Execute query
            mongo_projects = list(
                mongo_db["projects"]
                .find(mongo_query, {"_id": 0})
                .skip(skip)
                .limit(page_size)
            )

            total_projects = mongo_db["projects"].count_documents(mongo_query)

            # Add progress calculation to each project
            for project in mongo_projects:
                project["progress"] = calculate_progress(project.get("project_status"))
            
            return {
                "source": "mongo",
                "page": page,
                "page_size": page_size,
                "total_pages": (total_projects + page_size - 1) // page_size,
                "total_projects": total_projects,
                "data": mongo_projects,
                "filters_applied": {
                    "search": search_query or None,
                    "status": status or None,
                },
            }

        # Fallback to MySQL (only if MongoDB is not available)
        query = db.query(Project).filter(Project.is_deleted == False)

        # Apply search filter if provided
        if search_query:
            query = query.filter(
                or_(
                    Project.project_name.ilike(f"%{search_query}%"),
                    Project.description.ilike(f"%{search_query}%"),
                    Project.department.ilike(f"%{search_query}%"),
                )
            )

        # Apply status filter if provided
        if status:
            query = query.filter(Project.project_status == status)

        # Apply role-based access control
        query = apply_role_based_filter_projects(query, current_user)

        total_projects = query.count()
        mysql_projects = query.offset(skip).limit(page_size).all()

        projects_list = []
        for project in mysql_projects:
            project_dict = {
                k: v for k, v in project.__dict__.items() if not k.startswith("_")
            }
            # Add progress calculation
            project_dict["progress"] = calculate_progress(project.project_status)
            projects_list.append(project_dict)

        return {
            "source": "mysql",
            "page": page,
            "page_size": page_size,
            "total_pages": (total_projects + page_size - 1) // page_size,
            "total_projects": total_projects,
            "data": projects_list,
            "filters_applied": {
                "search": search_query or None,
                "status": status or None,
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching projects: {str(e)}"
        )


@router.get("/{project_id}", response_model=dict)
def get_project_by_id(
    project_id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get specific project by ID"""
    try:
        def calculate_progress(project_status):
            """Map project status to progress percentage"""
            status_progress = {
                "Not_Started": 0,
                "Not Started": 0,
                "ToDo": 25,
                "To Do": 25,
                "In_Progress": 75,
                "In Progress": 75,
                "Completed": 100,
            }
            return status_progress.get(project_status, 0)

        # Try MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            # Build MongoDB query with role-based access control
            mongo_query = {"is_deleted": {"$ne": True}}

            # Apply role-based access control
            mongo_query = apply_role_based_filter_mongo_projects(
                mongo_query, current_user
            )

            # Add project_id filter
            # Try int id first, then legacy string id
            mongo_query["id"] = project_id
            mongo_project = mongo_db["projects"].find_one(mongo_query, {"_id": 0})

            if not mongo_project:
                # Try legacy string id
                mongo_query["id"] = str(project_id)
                mongo_project = mongo_db["projects"].find_one(mongo_query, {"_id": 0})

            if mongo_project:
                mongo_project["progress"] = calculate_progress(mongo_project.get("project_status"))
                return {"source": "mongo", "project": mongo_project}
            else:
                # Return not found from MongoDB
                raise HTTPException(status_code=404, detail="Project not found")

        # Fallback to MySQL (only if MongoDB is not available)
        query = db.query(Project).filter(
            and_(Project.id == project_id, Project.is_deleted == False)
        )
        query = apply_role_based_filter_projects(query, current_user)

        project = query.first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_dict = {
            k: v for k, v in project.__dict__.items() if not k.startswith("_")
        }
        project_dict["progress"] = calculate_progress(project.project_status)
        return {"source": "mysql", "project": project_dict}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching project: {str(e)}")
    
    
@router.put("/{project_id}", response_model=dict)
def update_project(
    project_id: int,
    project: ProjectUpdateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Update project"""
    try:
        update_data = project.dict(exclude_unset=True)

        # 1. Check for project phase update and recalculate progress
        if "add_project_phase" in update_data:
            overall_progress = calculate_project_progress(
                update_data["add_project_phase"]
            )
            update_data["progress"] = (
                overall_progress  # Add/overwrite progress in update data
            )

        # Update Mongo FIRST
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            # Set updated_at field as well
            update_data["updated_at"] = datetime.utcnow()
            mongo_db["projects"].update_one({"id": project_id}, {"$set": update_data})

        # Then update MySQL (fallback/mirror)
        existing_project = (
            db.query(Project)
            .filter(and_(Project.id == project_id, Project.is_deleted == False))
            .first()
        )
        if not existing_project:
            # If not in MySQL but updated in Mongo, still succeed
            if mongo_client:
                return {
                    "message": "Project updated successfully",
                    "project_id": project_id,
                }
            raise HTTPException(status_code=404, detail="Project not found")
        # Allow Admin/SuperAdmin updates; restrict Employees by department membership
        if current_user.get("role") not in ("Admin", "SuperAdmin"):
            if existing_project.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="Access denied")

        # 2. Apply updates to the SQL model
        for key, value in update_data.items():
            if (
                key != "updated_at"
            ):  # updated_at will be handled implicitly by SQLAlchemy or set explicitly if needed
                setattr(existing_project, key, value)

        db.commit()
        return {"message": "Project updated successfully", "project_id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating project: {str(e)}")


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Soft delete project"""
    try:
        # Find project in MySQL, ensuring it's not already deleted
        existing_project = (
            db.query(Project)
            .filter(and_(Project.id == project_id, Project.is_deleted == False))
            .first()
        )

        # --- 1. Check Project Existence (MySQL and/or Mongo) ---
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        mongo_project_doc = None
        if mongo_db is not None:
            mongo_project_doc = mongo_db["projects"].find_one(
                {"id": project_id}
            ) or mongo_db["projects"].find_one({"id": str(project_id)})
        if existing_project is None and mongo_project_doc is None:
            raise HTTPException(status_code=404, detail="Project not found")

        # --- 2. Permission Check ---
        user_role = current_user.get("role")
        user_department = current_user.get("department")
        project_department = (
            existing_project.department
            if existing_project
            else (mongo_project_doc.get("department") if mongo_project_doc else None)
        )

        # Permission rules:
        # - SuperAdmin/Admin: allowed
        # - Employee: allowed if same department or member/admin of the project
        if user_role not in ("Admin", "SuperAdmin"):
            is_same_department = project_department == user_department
            is_member = False
            username = current_user.get("username")
            if existing_project is not None:
                try:
                    admins = existing_project.project_admins or []
                    members = existing_project.selected_team_members or []
                    is_member = (username in admins) or (username in members)
                except Exception:
                    is_member = False
            if not is_member and mongo_project_doc is not None:
                admins = mongo_project_doc.get("project_admins", [])
                members = mongo_project_doc.get("selected_team_members", [])
                is_member = (username in admins) or (username in members)
            if not (is_same_department or is_member):
                raise HTTPException(status_code=403, detail="Access denied")

        # --- 3. Soft Delete Execution ---

        # Soft delete in Mongo FIRST (verify result and fallback to string id)
        mongo_deleted = False
        if mongo_db is not None:
            result = mongo_db["projects"].update_one(
                {"id": project_id}, {"$set": {"is_deleted": True}}
            )
            if result.matched_count == 0:
                # fallback for legacy string ids
                result = mongo_db["projects"].update_one(
                    {"id": str(project_id)}, {"$set": {"is_deleted": True}}
                )
            mongo_deleted = result.modified_count > 0
            try:
                print(
                    f"delete_project id={project_id} mongo matched={result.matched_count} modified={result.modified_count}"
                )
            except Exception:
                pass

        # Then soft delete in MySQL
        sql_deleted = False
        if existing_project is not None and existing_project.is_deleted is not True:
            existing_project.is_deleted = True
            db.commit()
            sql_deleted = True

        if not (mongo_deleted or sql_deleted):
            raise HTTPException(
                status_code=404, detail="Project not found or already deleted"
            )

        return {
            "message": "Project deleted successfully",
            "mongo_deleted": mongo_deleted,
            "mysql_deleted": sql_deleted,
        }

    except HTTPException:
        # Re-raise explicit HTTP exceptions (404, 403)
        db.rollback()
        raise
    except Exception as e:
        # Rollback on generic errors
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting project: {str(e)}")


# ENHANCED ALERTS ENDPOINT
@router.get("/alerts/upcoming-deadlines", response_model=dict)
def get_upcoming_deadlines_alerts(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """Get automatic alerts for projects at 80% timeline"""
    try:
        current_time = datetime.now()
        alerts = []

        # Try MongoDB first for projects
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]

            # Get all active projects
            mongo_query = {
                "is_deleted": {"$ne": True},
                "project_status": {"$ne": "Completed"},
            }

            # Apply role-based filtering
            if current_user["role"] == "Admin" and current_user.get("department"):
                mongo_query["department"] = current_user["department"]
            elif current_user["role"] == "Employee":
                username = current_user["username"]
                mongo_query["$or"] = [
                    {"project_admins": {"$in": [username]}},
                    {"selected_team_members": {"$in": [username]}},
                ]

            projects = list(mongo_db["projects"].find(mongo_query, {"_id": 0}))

            for project in projects:
                deadline_str = project.get("deadline")
                if not deadline_str:
                    continue

                # Parse deadline
                try:
                    if isinstance(deadline_str, str):
                        deadline_dt = datetime.fromisoformat(
                            deadline_str.replace("Z", "+00:00")
                        )
                    else:
                        deadline_dt = deadline_str
                except Exception:
                    continue

                # Calculate 80% timeline
                project_start = project.get("created_at", current_time)
                if isinstance(project_start, str):
                    try:
                        project_start = datetime.fromisoformat(
                            project_start.replace("Z", "+00:00")
                        )
                    except Exception:
                        project_start = current_time

                total_duration = deadline_dt - project_start
                eighty_percent_time = project_start + (total_duration * 0.8)

                # Check if we're at or past 80% timeline
                if current_time >= eighty_percent_time:
                    days_remaining = (deadline_dt - current_time).days
                    hours_remaining = (
                        deadline_dt - current_time
                    ).total_seconds() / 3600

                    alerts.append(
                        {
                            "project_id": project.get("id"),
                            "project_name": project.get("project_name"),
                            "deadline": deadline_dt,
                            "days_remaining": max(0, days_remaining),
                            "hours_remaining": max(0, round(hours_remaining, 1)),
                            "timeline_percentage": min(
                                100,
                                round(
                                    ((current_time - project_start) / total_duration)
                                    * 100,
                                    1,
                                ),
                            ),
                            "alert_type": "80% timeline reached",
                            "priority": "High" if days_remaining <= 2 else "Medium",
                        }
                    )

            return {"alerts": alerts, "total_alerts": len(alerts)}

        # Fallback to MySQL
        query = db.query(Project).filter(
            and_(Project.is_deleted == False, Project.project_status != "Completed")
        )
        query = apply_role_based_filter_projects(query, current_user)
        projects = query.all()

        for project in projects:
            if not project.deadline:
                continue

            # Calculate 80% timeline
            project_start = project.created_at or current_time
            total_duration = project.deadline - project_start
            eighty_percent_time = project_start + (total_duration * 0.8)

            # Check if we're at or past 80% timeline
            if current_time >= eighty_percent_time:
                days_remaining = (project.deadline - current_time).days
                hours_remaining = (
                    project.deadline - current_time
                ).total_seconds() / 3600

                alerts.append(
                    {
                        "project_id": project.id,
                        "project_name": project.project_name,
                        "deadline": project.deadline,
                        "days_remaining": max(0, days_remaining),
                        "hours_remaining": max(0, round(hours_remaining, 1)),
                        "timeline_percentage": min(
                            100,
                            round(
                                ((current_time - project_start) / total_duration) * 100,
                                1,
                            ),
                        ),
                        "alert_type": "80% timeline reached",
                        "priority": "High" if days_remaining <= 2 else "Medium",
                    }
                )

        return {"alerts": alerts, "total_alerts": len(alerts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching alerts: {str(e)}")


# # Keep POST as is - for adding messages
# @router.post("/chat", response_model=ChatResponseSchema)
# def add_chat_message(
#     chat: ChatMessageSchema,
#     db: Session = Depends(get_mysql_session),
#     current_user: dict = Depends(get_current_user),
# ):
#     """Add chat message to project"""
#     try:
#         chat_doc = {
#             "project_id": chat.project_id,
#             "sender_name": chat.sender_name,
#             "sender_id": chat.sender_id,
#             "message": chat.message,
#             "timestamp": datetime.utcnow(),
#         }

#         mongo_client = get_mongo_client()
#         if mongo_client:
#             mongo_db = mongo_client[settings.MONGO_DB_NAME]
#             mongo_db["project_chats"].insert_one(chat_doc)

#         existing_project = (
#             db.query(Project).filter(Project.id == chat.project_id).first()
#         )
#         if existing_project:
#             # Create chat data for MySQL - exclude timestamp and any MongoDB _id
#             mysql_chat_data = {
#                 "project_id": chat_doc["project_id"],
#                 "sender_name": chat_doc["sender_name"],
#                 "sender_id": chat_doc["sender_id"],
#                 "message": chat_doc["message"],
#             }
#             new_chat = ProjectChat(**mysql_chat_data)
#             db.add(new_chat)
#             db.commit()
#             db.refresh(new_chat)
#             return ChatResponseSchema(
#                 id=new_chat.id,
#                 project_id=new_chat.project_id,
#                 sender_name=new_chat.sender_name,
#                 sender_id=new_chat.sender_id,
#                 message=new_chat.message,
#                 timestamp=new_chat.timestamp,
#             )

#         return ChatResponseSchema(
#             id=0,
#             project_id=chat_doc["project_id"],
#             sender_name=chat_doc["sender_name"],
#             sender_id=chat_doc["sender_id"],
#             message=chat_doc["message"],
#             timestamp=chat_doc["timestamp"],
#         )

#     except HTTPException:
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(
#             status_code=500, detail=f"Error adding chat message: {str(e)}"
#         )


# # Change GET to use a different path to avoid conflicts
# @router.get("/chat/messages", response_model=List[ChatResponseSchema])
# def get_project_chats(
#     project_id: int = Query(..., description="Project ID to get chat messages for"),
#     db: Session = Depends(get_mysql_session),
#     current_user: dict = Depends(get_current_user),
# ):
#     """
#     Get all chat messages for a project.
#     Example: GET /api/v1/projects/chat/messages?project_id=18
#     """
#     chats_list = []

#     try:
#         # Try MongoDB first
#         mongo_client = get_mongo_client()
#         if mongo_client:
#             mongo_db = mongo_client[settings.MONGO_DB_NAME]
#             docs = list(
#                 mongo_db["project_chats"]
#                 .find({"project_id": project_id})
#                 .sort("timestamp", 1)
#             )
#             for d in docs:
#                 chats_list.append(
#                     ChatResponseSchema(
#                         id=str(d.get("_id")),  # Keep as string for MongoDB ObjectId
#                         project_id=d["project_id"],
#                         sender_name=d.get("sender_name", "Unknown"),
#                         sender_id=d.get("sender_id", "0"),
#                         message=d.get("message", ""),
#                         timestamp=d.get("timestamp", datetime.utcnow()),
#                     )
#                 )

#         # Fallback to MySQL if MongoDB has no data
#         if not chats_list:
#             chats = (
#                 db.query(ProjectChat)
#                 .filter(ProjectChat.project_id == project_id)
#                 .order_by(ProjectChat.timestamp.asc())
#                 .all()
#             )
#             for c in chats:
#                 chats_list.append(
#                     ChatResponseSchema(
#                         id=c.id,  # Integer from MySQL
#                         project_id=c.project_id,
#                         sender_name=c.sender_name,
#                         sender_id=c.sender_id,
#                         message=c.message,
#                         timestamp=c.timestamp,
#                     )
#                 )

#         return chats_list

#     except Exception as e:
#         raise HTTPException(
#             status_code=500, detail=f"Error fetching chat messages: {str(e)}"
#         )



@router.websocket("/chat/ws/{project_id}")
async def project_chat_socket(websocket: WebSocket, project_id: int):
    await manager.connect(websocket, project_id)

    try:
        while True:
            raw_data = await websocket.receive_text()
            chat_data = json.loads(raw_data)

            msg_project_id = chat_data.get("project_id", project_id)
            sender_name = chat_data.get("sender_name", "Unknown")
            sender_id = chat_data.get("sender_id", "0")
            message = chat_data.get("message", "").strip()

            if not message:
                continue

            chat_doc = {
                "project_id": msg_project_id,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
            }

            # ✅ MongoDB insert
            mongo_client = get_mongo_client()
            if mongo_client:
                try:
                    mongo_db = mongo_client[settings.MONGO_DB_NAME]
                    result = mongo_db["project_chats"].insert_one(chat_doc)
                    chat_doc["_id"] = str(result.inserted_id)  # convert ObjectId to string
                except Exception as me:
                    print(f"⚠️ Mongo insert error: {me}")

            # ✅ MySQL insert
            try:
                db = next(get_mysql_session())
                sql_data = {
                    "project_id": msg_project_id,
                    "sender_name": sender_name,
                    "sender_id": sender_id,
                    "message": message,
                }
                new_chat = ProjectChat(**sql_data)
                db.add(new_chat)
                db.commit()
                db.close()
            except Exception as e:
                print(f"⚠️ MySQL insert error: {e}")

            # ✅ Broadcast message to all clients
            await manager.broadcast(chat_doc, msg_project_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id)
    except Exception as e:
        print(f"🔥 WebSocket error for Project {project_id}: {e}")
        manager.disconnect(websocket, project_id)
        await websocket.close()


@router.get("/chat/messages", response_model=List[ChatResponseSchema])
def get_project_chats(
    project_id: int = Query(..., description="Project ID to fetch chat messages for"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch all chat messages for a specific project.
    Works seamlessly with both MongoDB and MySQL.

    Example:
    GET /api/v1/projects/chat/messages?project_id=34
    """
    chats_list = []
    try:
        # ✅ Try MongoDB first (primary store for chats)
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            docs = list(
                mongo_db["project_chats"]
                .find({"project_id": project_id, "message": {"$exists": True}})
                .sort("timestamp", 1)
            )

            for d in docs:
                # Convert ObjectId to string, ensure timestamps are ISO strings
                chat_id = str(d.get("_id")) if d.get("_id") else None
                timestamp = d.get("timestamp")
                if isinstance(timestamp, datetime):
                    timestamp = timestamp.isoformat()

                chats_list.append(
                    ChatResponseSchema(
                        id=chat_id,
                        project_id=d.get("project_id", project_id),
                        sender_name=d.get("sender_name", "Unknown"),
                        sender_id=str(d.get("sender_id", "0")),
                        message=d.get("message", ""),
                        timestamp=timestamp or datetime.utcnow().isoformat(),
                    )
                )

        # ✅ Fallback: use MySQL if MongoDB has no messages
        if not chats_list:
            chats = (
                db.query(ProjectChat)
                .filter(ProjectChat.project_id == project_id)
                .order_by(ProjectChat.timestamp.asc())
                .all()
            )

            for c in chats:
                chats_list.append(
                    ChatResponseSchema(
                        id=str(c.id),
                        project_id=c.project_id,
                        sender_name=c.sender_name,
                        sender_id=str(c.sender_id),
                        message=c.message,
                        timestamp=c.timestamp.isoformat() if c.timestamp else None,
                    )
                )

        return chats_list

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat messages: {str(e)}"
        )
