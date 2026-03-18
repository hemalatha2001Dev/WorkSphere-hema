from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    JSON,
    UniqueConstraint,
    Index,
    func,
    ForeignKey,
    DateTime,
    Boolean,
    Float
)
from sqlalchemy.orm import relationship
from app.db.mysql import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    firstname = Column(String(50), nullable=False)
    lastname = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    phone = Column(String(15), nullable=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    employee_id = Column(String(50), nullable=True, index=True)
    position = Column(String(50), nullable=True)
    designation = Column(String(50), nullable=True)
    department = Column(String(50), nullable=True, index=True)
    role = Column(String(50), nullable=True, index=True)
    status = Column(String(10), default="Active")
    blood_group = Column(String(10), nullable=True)
    joining_date = Column(String(50), nullable=True)
    date_of_birth = Column(String(50), nullable=True)
    profile_image = Column(String(255), nullable=True)
    permanent_address = Column(Text, nullable=True)
    current_address = Column(Text, nullable=True)
    emergency_contact_name = Column(String(100), nullable=True)
    emergency_contact_number = Column(String(15), nullable=True)
    emergency_contact_relation = Column(String(50), nullable=True)
    custom_fields = Column(JSON, nullable=True)
    password = Column(String(255), nullable=False)

    __table_args__ = (
        UniqueConstraint('email', name='uq_user_email'),
        UniqueConstraint('username', name='uq_user_username'),
        Index('idx_department', 'department'),
        Index('idx_role', 'role'),
    )


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String(255), nullable=False)
    description = Column(Text)
    add_project_phase = Column(JSON)
    department = Column(String(255))  # Match DB: varchar(255)
    project_admins = Column(JSON)
    selected_team_members = Column(JSON)
    assigned_teams = Column(JSON)
    project_status = Column(String(50))
    priority = Column(String(50))
    start_date = Column(String(50), nullable=True)  # ADD THIS LINE
    deadline = Column(String(50))  # Match DB: varchar(50) for now
    project_files = Column(JSON, default=[])
    progress = Column(Integer, default=0)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    is_deleted = Column(Boolean, default=False)

    # Relationship to files
    files = relationship(
        "ProjectFile",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    
    # Relationship to chats
    chats = relationship(
        "ProjectChat",
        back_populates="project",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_project_status', 'project_status'),
        Index('idx_project_department', 'department'),
        Index('idx_project_priority', 'priority'),
        Index('idx_project_deadline', 'deadline'),
    )


class ProjectFile(Base):
    __tablename__ = "project_files"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=True)  # pdf, png, doc, etc.
    file_size = Column(Integer, nullable=True)  # File size in bytes
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to project
    project = relationship("Project", back_populates="files")


class ProjectChat(Base):
    __tablename__ = "project_chats"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String(50), nullable=False)  # add this
    sender_name = Column(String(255), nullable=False)
    sender_role = Column(String(100), nullable=True)
    sender_department = Column(String(100), nullable=True)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to project
    project = relationship("Project", back_populates="chats")

    __table_args__ = (
        Index('idx_chat_project', 'project_id'),
        Index('idx_chat_timestamp', 'timestamp'),
    )

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    project_lists = Column(JSON, nullable=True)  # Project IDs as integers
    project_phases = Column(JSON, nullable=True)
    status = Column(String(50), default="Pending")
    updated_status = Column(String, nullable=True)  # ADD THIS LINE
    original_status = Column(String, nullable=True)  # ADD THIS LINE - Stores status before employee submission
    priority = Column(String(50), default="Medium")
    assignee = Column(String(100), nullable=True)
    selected_team_members = Column(JSON, nullable=True)  # Employee IDs
    select_time_range = Column(JSON, nullable=True)
    tasks_files = Column(JSON, nullable=True)  # NEW: File paths/URLs
    custom_fields = Column(JSON, nullable=True)  # NEW: Custom fields as JSON
    progress_percentage = Column(Integer, default=0)
    is_deleted = Column(Boolean, default=False)
    created_by = Column(String(100), nullable=True)
    department = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deadline = Column(DateTime(timezone=True), nullable=True)


    activities = relationship("TaskActivity", back_populates="task", cascade="all, delete-orphan")
    chats = relationship("TaskChat", back_populates="task", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_task_status', 'status'),
        Index('idx_task_priority', 'priority'),
        Index('idx_task_assignee', 'assignee'),
        Index('idx_task_department', 'department'),
        Index('idx_task_deadline', 'deadline'),
    )


class TaskActivity(Base):
    __tablename__ = "task_activities"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    task_name = Column(String(255), nullable=False)
    task_completed_by = Column(String(100), nullable=True)
    task_title = Column(String(255), nullable=True)
    task_description = Column(Text, nullable=True)
    task_status_updation_time_stamp = Column(DateTime(timezone=True), server_default=func.now())
    activity_type = Column(String(50), nullable=False)  # status_update, assignment, etc.

    # Relationship back to task
    task = relationship("Task", back_populates="activities")


class TaskChat(Base):
    __tablename__ = "task_chats"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String(50), nullable=False)  # add this
    sender_name = Column(String(255), nullable=False)
    sender_role = Column(String(100), nullable=True)
    sender_department = Column(String(100), nullable=True)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to task
    task = relationship("Task", back_populates="chats")

    __table_args__ = (
        Index('idx_task_chat_timestamp', 'timestamp'),
        Index('idx_task_chat_task', 'task_id'),
    )