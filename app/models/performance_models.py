from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from app.db.mysql import Base

class SalaryConfiguration(Base):
    __tablename__ = "salary_configurations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(100), unique=True, index=True, nullable=False)
    base_salary = Column(Float, default=0.0)
    allowances = Column(Float, default=0.0)
    deductions = Column(Float, default=0.0)
    bank_name = Column(String(100), nullable=True)
    account_number = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Objective(Base):
    __tablename__ = "objectives"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    employee_id = Column(String(100), index=True, nullable=False)
    department = Column(String(100), index=True, nullable=True)
    start_date = Column(String(50), nullable=True)
    target_date = Column(String(50), nullable=True)
    progress = Column(Float, default=0.0)
    status = Column(String(50), default="Pending")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationship to key results
    key_results = relationship(
        "KeyResult",
        back_populates="objective",
        cascade="all, delete-orphan"
    )


class KeyResult(Base):
    __tablename__ = "key_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    objective_id = Column(Integer, ForeignKey("objectives.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    target_value = Column(Float, default=0.0)
    current_value = Column(Float, default=0.0)
    unit = Column(String(50), nullable=True)
    weight = Column(Float, default=1.0)
    progress = Column(Float, default=0.0)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationship back to objective
    objective = relationship("Objective", back_populates="key_results")


class PerformanceReview(Base):
    __tablename__ = "performance_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(100), index=True, nullable=False)
    reviewer_id = Column(String(100), nullable=False)
    review_period = Column(String(50), index=True, nullable=False)
    task_completion_rate = Column(Float, default=0.0)
    okr_completion_rate = Column(Float, default=0.0)
    timesheet_hours = Column(Float, default=0.0)
    calculated_score = Column(Float, default=0.0)
    allocated_rating = Column(String(50), nullable=True)
    manager_feedback = Column(Text, nullable=True)
    status = Column(String(50), default="Draft")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class PayrollRecord(Base):
    __tablename__ = "payroll_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(100), index=True, nullable=False)
    month = Column(String(7), index=True, nullable=False) # Format: YYYY-MM
    present_days = Column(Integer, default=0)
    absent_days = Column(Integer, default=0)
    total_working_days = Column(Integer, default=0)
    base_salary = Column(Float, default=0.0)
    allowances = Column(Float, default=0.0)
    deductions = Column(Float, default=0.0)
    net_salary = Column(Float, default=0.0)
    status = Column(String(50), default="Draft")
    payment_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


# ==================== NEW MODELS ====================

class KPIRecord(Base):
    """Individual KPI tracking per employee. Feeds into performance score."""
    __tablename__ = "kpi_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(100), index=True, nullable=False)
    kpi_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    target_value = Column(Float, default=0.0)
    actual_value = Column(Float, default=0.0)
    unit = Column(String(50), nullable=True)          # e.g. %, count, hours
    weight = Column(Float, default=1.0)               # Relative importance
    achievement_rate = Column(Float, default=0.0)     # (actual/target)*100
    review_period = Column(String(50), index=True, nullable=False)  # e.g. Q1 2026
    category = Column(String(100), nullable=True)     # e.g. Sales, Quality, Delivery
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class RatingAllocation(Base):
    """
    Automated Rating Allocation — stores system-calculated ratings
    plus manager review/approval workflow.
    """
    __tablename__ = "rating_allocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(100), index=True, nullable=False)
    review_period = Column(String(50), index=True, nullable=False)  # e.g. Q1 2026

    # Scores used to compute the rating
    task_completion_rate = Column(Float, default=0.0)
    okr_completion_rate = Column(Float, default=0.0)
    timesheet_hours = Column(Float, default=0.0)
    kpi_score = Column(Float, default=0.0)            # Average KPI achievement rate
    calculated_score = Column(Float, default=0.0)    # Weighted final score (0–100)

    # System-suggested rating
    recommended_rating = Column(String(50), nullable=True)   # e.g. Outstanding (5/5)
    recommended_rating_value = Column(Float, default=0.0)    # 1–5 numeric

    # Manager decision
    allocated_rating = Column(String(50), nullable=True)     # Final rating after review
    allocated_rating_value = Column(Float, default=0.0)
    manager_id = Column(String(100), nullable=True)
    manager_comments = Column(Text, nullable=True)

    # Workflow status: Auto-Generated → Pending Review → Approved / Rejected
    status = Column(String(50), default="Auto-Generated", index=True)
    is_approved = Column(Boolean, default=False)

    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
