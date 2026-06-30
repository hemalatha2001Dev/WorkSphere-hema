from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel

from app.db.performance_db import get_performance_db
from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.core.config import settings
from app.models.performance_models import RatingAllocation, Objective, KPIRecord
from app.models.mysql_models import Task, User
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/rating", tags=["Automated Rating Allocation"])

# ==================== HELPERS ====================

RATING_SCALE = [
    (90, "Outstanding (5/5)", 5.0),
    (80, "Exceeds Expectations (4/5)", 4.0),
    (60, "Meets Expectations (3/5)", 3.0),
    (40, "Needs Improvement (2/5)", 2.0),
    (0,  "Unsatisfactory (1/5)", 1.0),
]

def score_to_rating(score: float):
    for threshold, label, value in RATING_SCALE:
        if score >= threshold:
            return label, value
    return "Unsatisfactory (1/5)", 1.0


def get_months_in_range(start_date_str: str, end_date_str: str) -> List[str]:
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        months = []
        current = start_date
        while current <= end_date:
            month_str = current.strftime("%Y-%m")
            if month_str not in months:
                months.append(month_str)
            current += timedelta(days=28)
        end_month = end_date.strftime("%Y-%m")
        if end_month not in months:
            months.append(end_month)
        return months
    except Exception:
        return [datetime.utcnow().strftime("%Y-%m")]


def compute_all_scores(
    employee_id: str,
    start_date_str: str,
    end_date_str: str,
    review_period: str,
    db_sqlite: Session,
    db_mysql: Session
) -> dict:
    """Compute task, OKR, timesheet, KPI scores and produce a weighted final score."""
    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # 1. Task Completion Rate (MySQL)
    tasks = db_mysql.query(Task).filter(
        Task.is_deleted == False,
        Task.assignee == employee_id,
        Task.created_at >= start_dt,
        Task.created_at <= end_dt
    ).all()
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == "Completed")
    task_rate = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else 0.0

    # 2. OKR Completion Rate (SQLite)
    objectives = db_sqlite.query(Objective).filter(
        Objective.employee_id == employee_id
    ).all()
    total_objs = len(objectives)
    okr_rate = (sum(o.progress for o in objectives) / total_objs) if total_objs > 0 else 0.0

    # 3. Timesheet Hours (MongoDB)
    timesheet_hours = 0.0
    months = get_months_in_range(start_date_str, end_date_str)
    try:
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            timesheets = list(mongo_db["timesheets"].find({
                "employee_id": employee_id,
                "month": {"$in": months}
            }))
            for ts in timesheets:
                for entry in ts.get("entries", []):
                    entry_date_str = entry.get("date")
                    if entry_date_str:
                        try:
                            entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d")
                            if start_dt <= entry_dt <= end_dt:
                                timesheet_hours += entry.get("hours_spent", 0)
                        except ValueError:
                            timesheet_hours += entry.get("hours_spent", 0)
                    else:
                        timesheet_hours += entry.get("hours_spent", 0)
    except Exception as e:
        print(f"Warning: Could not fetch timesheet data: {e}")

    target_hours = 160.0 * len(months)
    timesheet_ratio = min(timesheet_hours / target_hours, 1.0) if target_hours > 0 else 0.0
    timesheet_score = timesheet_ratio * 100.0

    # 4. KPI Score (SQLite kpi_records for this review_period)
    kpis = db_sqlite.query(KPIRecord).filter(
        KPIRecord.employee_id == employee_id,
        KPIRecord.review_period == review_period
    ).all()
    if kpis:
        total_weight = sum(k.weight for k in kpis)
        weighted_kpi = sum(k.achievement_rate * k.weight for k in kpis)
        kpi_score = round(weighted_kpi / total_weight, 2) if total_weight > 0 else 0.0
    else:
        kpi_score = 0.0

    # 5. Weighted Final Score: Tasks 30%, OKR 30%, Timesheet 20%, KPI 20%
    if kpis:
        calculated_score = round(
            (task_rate * 0.30) + (okr_rate * 0.30) + (timesheet_score * 0.20) + (kpi_score * 0.20), 2
        )
    else:
        # Fallback: Tasks 40%, OKR 40%, Timesheet 20%
        calculated_score = round(
            (task_rate * 0.40) + (okr_rate * 0.40) + (timesheet_score * 0.20), 2
        )

    return {
        "task_completion_rate": round(task_rate, 2),
        "okr_completion_rate": round(okr_rate, 2),
        "timesheet_hours": round(timesheet_hours, 2),
        "kpi_score": kpi_score,
        "calculated_score": calculated_score
    }


# ==================== SCHEMAS ====================

class AutoAllocateSchema(BaseModel):
    employee_id: str
    review_period: str      # e.g. "Q1 2026"
    start_date: str         # YYYY-MM-DD
    end_date: str           # YYYY-MM-DD

class ManagerReviewSchema(BaseModel):
    allocated_rating: str   # Manager's chosen rating label
    manager_comments: Optional[str] = None
    action: str             # "approve" or "reject"

class RatingAllocationResponseSchema(BaseModel):
    id: int
    employee_id: str
    review_period: str
    task_completion_rate: float
    okr_completion_rate: float
    timesheet_hours: float
    kpi_score: float
    calculated_score: float
    recommended_rating: Optional[str]
    recommended_rating_value: float
    allocated_rating: Optional[str]
    allocated_rating_value: float
    manager_id: Optional[str]
    manager_comments: Optional[str]
    status: str
    is_approved: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    approved_at: Optional[datetime]

    class Config:
        from_attributes = True


# ==================== ENDPOINTS ====================

@router.post("/auto-allocate", response_model=RatingAllocationResponseSchema, status_code=status.HTTP_201_CREATED)
def auto_allocate_rating(
    payload: AutoAllocateSchema,
    db_sqlite: Session = Depends(get_performance_db),
    db_mysql: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Automatically calculate and create a rating allocation for an employee based on:
    Task completion + OKR progress + Timesheet hours + KPI achievement.
    Admin/SuperAdmin only.
    """
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can auto-allocate ratings.")

    # Check if allocation already exists for this employee + period
    existing = db_sqlite.query(RatingAllocation).filter(
        RatingAllocation.employee_id == payload.employee_id,
        RatingAllocation.review_period == payload.review_period
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Rating allocation already exists for employee '{payload.employee_id}' in period '{payload.review_period}'. Use PUT to update."
        )

    scores = compute_all_scores(
        employee_id=payload.employee_id,
        start_date_str=payload.start_date,
        end_date_str=payload.end_date,
        review_period=payload.review_period,
        db_sqlite=db_sqlite,
        db_mysql=db_mysql
    )

    rec_label, rec_value = score_to_rating(scores["calculated_score"])

    allocation = RatingAllocation(
        employee_id=payload.employee_id,
        review_period=payload.review_period,
        task_completion_rate=scores["task_completion_rate"],
        okr_completion_rate=scores["okr_completion_rate"],
        timesheet_hours=scores["timesheet_hours"],
        kpi_score=scores["kpi_score"],
        calculated_score=scores["calculated_score"],
        recommended_rating=rec_label,
        recommended_rating_value=rec_value,
        allocated_rating=rec_label,       # Pre-fill with recommendation; manager can override
        allocated_rating_value=rec_value,
        status="Pending Review",
        is_approved=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db_sqlite.add(allocation)
    db_sqlite.commit()
    db_sqlite.refresh(allocation)
    return allocation


@router.get("/allocations", response_model=List[RatingAllocationResponseSchema])
def list_rating_allocations(
    employee_id: Optional[str] = Query(None),
    review_period: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """List all rating allocations with optional filters. Employees only see their own."""
    query = db.query(RatingAllocation)
    if current_user["role"] == "Employee":
        query = query.filter(RatingAllocation.employee_id == current_user.get("employee_id"))
    else:
        if employee_id:
            query = query.filter(RatingAllocation.employee_id == employee_id)
    if review_period:
        query = query.filter(RatingAllocation.review_period == review_period)
    if status:
        query = query.filter(RatingAllocation.status == status)
    return query.order_by(RatingAllocation.created_at.desc()).all()


@router.get("/allocations/{id}", response_model=RatingAllocationResponseSchema)
def get_rating_allocation(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get a specific rating allocation."""
    allocation = db.query(RatingAllocation).filter(RatingAllocation.id == id).first()
    if not allocation:
        raise HTTPException(status_code=404, detail="Rating allocation not found.")
    if current_user["role"] == "Employee" and current_user.get("employee_id") != allocation.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return allocation


@router.put("/allocations/{id}/approve", response_model=RatingAllocationResponseSchema)
def manager_review_rating(
    id: int,
    payload: ManagerReviewSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manager approves or rejects a rating allocation.
    - action='approve': sets is_approved=True, status='Approved', stores manager's final rating.
    - action='reject': sets status='Rejected', keeps is_approved=False.
    Admin/SuperAdmin only.
    """
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can review ratings.")

    allocation = db.query(RatingAllocation).filter(RatingAllocation.id == id).first()
    if not allocation:
        raise HTTPException(status_code=404, detail="Rating allocation not found.")
    if allocation.status == "Approved":
        raise HTTPException(status_code=400, detail="This rating has already been approved.")

    if payload.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'.")

    allocation.manager_id = current_user.get("employee_id") or current_user.get("username")
    allocation.manager_comments = payload.manager_comments
    allocation.updated_at = datetime.utcnow()

    if payload.action == "approve":
        allocation.allocated_rating = payload.allocated_rating
        _, rating_value = score_to_rating(0)  # default
        for threshold, label, value in RATING_SCALE:
            if payload.allocated_rating == label:
                rating_value = value
                break
        allocation.allocated_rating_value = rating_value
        allocation.status = "Approved"
        allocation.is_approved = True
        allocation.approved_at = datetime.utcnow()
    else:
        allocation.status = "Rejected"
        allocation.is_approved = False

    db.commit()
    db.refresh(allocation)
    return allocation


@router.get("/history/{employee_id}", response_model=List[RatingAllocationResponseSchema])
def get_rating_history(
    employee_id: str,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get full rating allocation history for an employee, newest first."""
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    return (
        db.query(RatingAllocation)
        .filter(RatingAllocation.employee_id == employee_id)
        .order_by(RatingAllocation.created_at.desc())
        .all()
    )


@router.get("/reports/distribution", response_model=dict)
def get_rating_distribution_report(
    review_period: Optional[str] = Query(None, description="e.g. Q1 2026"),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get rating distribution report across all employees.
    Shows counts, percentages, and average scores per rating level. Admin/SuperAdmin only.
    """
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins and SuperAdmins can view distribution reports.")

    query = db.query(RatingAllocation).filter(RatingAllocation.is_approved == True)
    if review_period:
        query = query.filter(RatingAllocation.review_period == review_period)

    allocations = query.all()
    total = len(allocations)

    distribution = {}
    score_sum = 0.0
    for a in allocations:
        score_sum += a.calculated_score or 0.0
        label = a.allocated_rating or "Unrated"
        if label not in distribution:
            distribution[label] = {"count": 0, "total_score": 0.0, "rating_value": a.allocated_rating_value or 0.0}
        distribution[label]["count"] += 1
        distribution[label]["total_score"] += a.calculated_score or 0.0

    # Compute percentages and average scores per bucket
    result_distribution = {}
    for label, data in distribution.items():
        result_distribution[label] = {
            "count": data["count"],
            "percentage": round((data["count"] / total) * 100.0, 2) if total > 0 else 0.0,
            "average_score": round(data["total_score"] / data["count"], 2) if data["count"] > 0 else 0.0,
            "rating_value": data["rating_value"]
        }

    # Pending (unapproved) count
    pending_query = db.query(RatingAllocation).filter(RatingAllocation.status == "Pending Review")
    if review_period:
        pending_query = pending_query.filter(RatingAllocation.review_period == review_period)
    pending_count = pending_query.count()

    return {
        "review_period": review_period or "All Periods",
        "total_approved_ratings": total,
        "total_pending_review": pending_count,
        "average_calculated_score": round(score_sum / total, 2) if total > 0 else 0.0,
        "rating_distribution": result_distribution,
        "generated_at": datetime.utcnow()
    }
