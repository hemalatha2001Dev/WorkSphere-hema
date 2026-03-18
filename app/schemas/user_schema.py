from pydantic import BaseModel, EmailStr,field_validator,Field
from typing import Optional, Literal, Dict, List, Union ,Any
from datetime import datetime

from sqlalchemy import union


class CustomField(BaseModel):
    name: str
    value: str


class RegisterSchema(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    phone: str
    employee_id: str
    department: str
    role: str
    position: str
    joining_date: str
    date_of_birth: str
    status: Literal["Active", "Inactive"]
    blood_group: str
    permanent_address: str
    current_address: str
    emergency_contact_name: str
    emergency_contact_number: str
    emergency_contact_relation: str
    username: str
    file_mongo_id: Optional[str]
    file_name: Optional[str]
    file_content_type: Optional[str]


class UserOut(RegisterSchema):
    pass


class LoginSchema(BaseModel):
    username: str
    password: str






class EmployeeRegisterSchema(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    phone: str
    username: str
    employee_id: str
    designation: str
    department: str
    role: str
    status: Literal["Active", "Inactive"]
    blood_group: str
    joining_date: str
    date_of_birth: str
    profile_image: Optional[str] = None
    permanent_address: str
    current_address: str
    emergency_contact_name: str
    emergency_contact_number: str
    emergency_contact_relation: str
    custom_fields: Optional[Union[List[CustomField], dict]] = None


class EmployeeOutSchema(EmployeeRegisterSchema):
    pass


class EmployeeUpdateSchema(BaseModel):
    employee_id: str
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    designation: Optional[str] = None
    status: Optional[str] = None
    blood_group: Optional[str] = None
    joining_date: Optional[str] = None
    date_of_birth: Optional[str] = None
    profile_image: Optional[str] = None
    permanent_address: Optional[str] = None
    current_address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_number: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    custom_fields: Optional[Union[List[CustomField], dict]] = None






class ProjectFileSchema(BaseModel):
    file_name: str
    file_url: str
    file_type: str
    uploaded_at: str

class ProjectPhaseSchema(BaseModel):
    phase_name: str
    start_date: str  # Added start_date field
    end_date: str
    progress: float
    status: str = "Not Started"
    
    class Config:
        extra = "allow"  # Allow additional fields


class ProjectCreateSchema(BaseModel):
    project_name: str
    description: str
    add_project_phase: Optional[List[Dict[str, Any]]] = Field(default_factory=list)  # Default to empty list, not None
    department: str
    project_admins: List[str]
    selected_team_members: List[str]
    assigned_teams: List[str] = []
    project_status: str
    priority: str
    start_date: Optional[str] = None  # Added start_date
    deadline: str  # This is end_date
    project_files: List[str] = []

    @field_validator('add_project_phase', mode='before')
    @classmethod
    def handle_phases(cls, v: Optional[List[Any]]):
        """Convert None to empty list, keep everything else as-is"""
        if v is None:
            return []  # Convert null to empty list
        if not isinstance(v, list):
            return []  # If not a list, return empty list
        return v  # Return the list as-is, accepting any data types


class ProjectUpdateSchema(BaseModel):
    project_name: Optional[str] = None
    description: Optional[str] = None
    add_project_phase: Optional[List[Dict[str, Any]]] = None
    department: Optional[str] = None
    project_admins: Optional[List[str]] = None
    selected_team_members: Optional[List[str]] = None
    assigned_teams: Optional[List[str]] = None
    project_status: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None
    progress: Optional[float] = None

    @field_validator('add_project_phase', mode='before')
    @classmethod
    def handle_phases_update(cls, v: Optional[List[Any]]):
        """Convert None to empty list, keep everything else as-is"""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v 
        
class ProjectResponseSchema(BaseModel):
    id: int
    project_name: str
    description: str
    add_project_phase: Optional[List[dict]] = None  # Changed to Optional[List[dict]]
    department: str
    project_admins: List[str]
    selected_team_members: List[str]
    assigned_teams: List[str]
    project_status: str
    priority: str
    deadline: Optional[str] = None  # Changed to Optional[str]
    project_files: List[str]
    created_by: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ChatMessageSchema(BaseModel):
    project_id: int
    sender_name: str  # This will be username
    sender_id: str    # This will be employee_id
    message: str


class ChatResponseSchema(BaseModel):
    id: Union[int, str]
    project_id: int
    sender_name: str
    sender_id: str
    sender_role: Optional[str] = None
    sender_department: Optional[str] = None
    message: str
    timestamp: datetime


class DashboardMetricsSchema(BaseModel):
    total_projects: int
    average_progress: float
    completed: int
    urgent: int


class MonthlyProgressSchema(BaseModel):
    month: str
    completed: int
    inprogress: int
    planning: int


class ProjectStatusDistributionSchema(BaseModel):
    completed: int
    inprogress: int
    planning: int


class AlertSchema(BaseModel):
    project_id: int
    project_name: str
    deadline: str
    priority: str
    department: str
    days_remaining: int


# Response schemas for pagination
class PaginatedResponse(BaseModel):
    page: int
    page_size: int
    total_pages: int
    total_items: int
    data: List


class ProjectsResponse(PaginatedResponse):
    data: List[ProjectResponseSchema]


class EmployeesResponse(PaginatedResponse):
    data: List[EmployeeOutSchema]




class TimeRangeSchema(BaseModel):
    start_date: str
    end_date: str

# Update your schemas in user_schema.py
class TaskCreateSchema(BaseModel):
    task_title: str
    description: Optional[str] = None
    project_lists: Optional[List[int]] = []
    project_phases: Optional[List[str]] = []
    status: str = "Pending"
    priority: str = "Medium"
    assignee: Optional[str] = None
    selected_team_members: Optional[List[str]] = []
    select_time_range: Optional[TimeRangeSchema] = None
    tasks_files: Optional[List[str]] = []
    custom_fields: Optional[List[CustomField]] = []
    deadline: Optional[str] = None
    original_status: Optional[str] = None
    updated_status: Optional[str] = None


class TaskUpdateSchema(BaseModel):
    task_title: Optional[str] = None
    description: Optional[str] = None
    project_lists: Optional[List[int]] = None
    project_phases: Optional[List[str]] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    selected_team_members: Optional[List[str]] = None
    select_time_range: Optional[TimeRangeSchema] = None
    tasks_files: Optional[List[str]] = None
    custom_fields: Optional[List[CustomField]] = []
    progress_percentage: Optional[int] = None
    deadline: Optional[str] = None
    original_status: Optional[str] = None
    updated_status: Optional[str] = None


class TaskResponseSchema(BaseModel):
    id: int
    task_title: str
    description: Optional[str]
    project_lists: Optional[List[int]]
    project_phases: Optional[List[str]]
    status: str
    priority: str
    assignee: Optional[str]
    selected_team_members: Optional[List[str]]
    select_time_range: Optional[Dict[str, str]]
    tasks_files: Optional[List[str]]
    custom_fields: Optional[List[CustomField]] = []
    progress_percentage: int
    created_by: str
    department: Optional[str]
    deadline: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    original_status: Optional[str] = None  # Status before employee submission
    updated_status: Optional[str] = None  # Employee's proposed status
    current_status: Optional[str] = None  # Display: original status for frontend

class TaskChatSchema(BaseModel):
    task_id: int  # Changed to str to handle alphanumeric IDs
    sender_name: str  # This will be username
    sender_id: str    # This will be employee_id
    message: str

class TaskChatResponseSchema(BaseModel):
    id: Union[int, str]
    task_id: str  # Changed to str to handle alphanumeric IDs
    sender_name: str
    sender_id: str
    sender_role: Optional[str] = None
    sender_department: Optional[str] = None
    message: str
    timestamp: datetime

# Dashboard Schemas
class DashboardMetricsSchema(BaseModel):
    """Schema for the main dashboard metrics."""
    total_tasks: int
    completed: int
    in_progress: int
    todo: int
    pending: int

class PieChartDistributionSchema(BaseModel):
    """Schema for the task status distribution used in pie charts."""
    in_progress: int
    completed: int
    pending: int
    todo: int

class WeekWiseDistributionSchema(BaseModel):
    """Schema for weekly task distribution (completed and in-progress)."""
    week_label: str  # e.g., "Oct 1 - Oct 7"
    completed: int
    in_progress: int

class RecentTaskSchema(BaseModel):
    task_name: str
    task_status: str
    task_progress_percentage: int

class TeamActivitySchema(BaseModel):
    task_name: str
    task_completed_by: str
    task_title: str
    task_description: str
    task_status_updation_time_stamp: datetime

class AlertSchema(BaseModel):
    task_name: str
    due_time_stamp: datetime
    days_remaining: int


class NotificationSchema(BaseModel):
    id: str
    type: str  
    title: str
    message: str
    entity_id: str  # task_id or project_id
    entity_name: str  # task_title or project_name
    status: str  # 'read' or 'unread'
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NotificationResponseSchema(BaseModel):
    notifications: List[NotificationSchema]
    total: int
    unread_count: int
