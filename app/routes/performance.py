from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel

from app.db.performance_db import get_performance_db
from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.core.config import settings
from app.models.performance_models import PerformanceReview, Objective, KPIRecord
from app.models.mysql_models import Task, User
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/performance", tags=["Performance"])

# ==================== SCHEMAS ====================

class ReviewCreateSchema(BaseModel):
    employee_id: str
    review_period: str # e.g. "Q1 2026"
    manager_feedback: Optional[str] = None
    allocated_rating: Optional[str] = None
    status: Optional[str] = "Draft"
    start_date: str # YYYY-MM-DD
    end_date: str   # YYYY-MM-DD

class ReviewUpdateSchema(BaseModel):
    manager_feedback: Optional[str] = None
    allocated_rating: Optional[str] = None
    status: Optional[str] = None # e.g. Draft, Submitted, Approved
    task_completion_rate: Optional[float] = None
    okr_completion_rate: Optional[float] = None
    timesheet_hours: Optional[float] = None
    calculated_score: Optional[float] = None

class CalculationRequestSchema(BaseModel):
    employee_id: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD

class ReviewResponseSchema(BaseModel):
    id: int
    employee_id: str
    reviewer_id: str
    review_period: str
    task_completion_rate: float
    okr_completion_rate: float
    timesheet_hours: float
    calculated_score: float
    allocated_rating: Optional[str]
    manager_feedback: Optional[str]
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# ==================== HELPERS ====================

def get_months_in_range(start_date_str: str, end_date_str: str) -> List[str]:
    """Get list of YYYY-MM months within the start and end dates"""
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


def compute_performance_metrics(
    employee_id: str,
    start_date_str: str,
    end_date_str: str,
    db_sqlite: Session,
    db_mysql: Session
) -> dict:
    """Helper to query all sources and compute performance metrics"""
    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # 1. Task Completion Rate (MySQL Tasks)
    # Filter tasks where employee is assignee or in selected_team_members, within date range
    tasks = db_mysql.query(Task).filter(
        Task.is_deleted == False,
        Task.assignee == employee_id,
        Task.created_at >= start_dt,
        Task.created_at <= end_dt
    ).all()
    
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == "Completed")
    task_completion_rate = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else 0.0

    # 2. OKR Completion Rate (SQLite Objectives)
    # Average progress of objectives created/active in period
    objectives = db_sqlite.query(Objective).filter(
        Objective.employee_id == employee_id
    ).all()
    
    total_objs = len(objectives)
    okr_completion_rate = (sum(o.progress for o in objectives) / total_objs) if total_objs > 0 else 0.0

    # 3. Timesheet Hours (MongoDB Timesheets)
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
                    # Filter by date if entry has date
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
        print(f"Error fetching timesheet hours: {e}")

    # 4. Score Calculation
    # Weights: Tasks (40%), OKRs (40%), Timesheet (20%)
    # Target hours: 160 hours per month
    target_hours = 160.0 * len(months)
    timesheet_ratio = min(timesheet_hours / target_hours, 1.0) if target_hours > 0 else 0.0
    timesheet_score = timesheet_ratio * 100.0

    calculated_score = round(
        (task_completion_rate * 0.4) +
        (okr_completion_rate * 0.4) +
        (timesheet_score * 0.2), 
        2
    )

    # 5. Rating Recommendation
    if calculated_score >= 90:
        recommended_rating = "Outstanding (5/5)"
    elif calculated_score >= 80:
        recommended_rating = "Exceeds Expectations (4/5)"
    elif calculated_score >= 60:
        recommended_rating = "Meets Expectations (3/5)"
    elif calculated_score >= 40:
        recommended_rating = "Needs Improvement (2/5)"
    else:
        recommended_rating = "Unsatisfactory (1/5)"

    return {
        "task_completion_rate": round(task_completion_rate, 2),
        "okr_completion_rate": round(okr_completion_rate, 2),
        "timesheet_hours": round(timesheet_hours, 2),
        "calculated_score": calculated_score,
        "recommended_rating": recommended_rating
    }

# ==================== ENDPOINTS ====================

@router.post("/reviews/calculate", response_model=dict)
def get_calculated_metrics(
    payload: CalculationRequestSchema,
    db_sqlite: Session = Depends(get_performance_db),
    db_mysql: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Calculate and return live performance metrics for an employee in a given range"""
    # Permission: employee can check themselves, managers can check anyone
    if current_user["role"] == "Employee" and current_user.get("employee_id") != payload.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    return compute_performance_metrics(
        employee_id=payload.employee_id,
        start_date_str=payload.start_date,
        end_date_str=payload.end_date,
        db_sqlite=db_sqlite,
        db_mysql=db_mysql
    )


@router.post("/reviews", response_model=ReviewResponseSchema, status_code=status.HTTP_201_CREATED)
def create_review(
    review_data: ReviewCreateSchema,
    db_sqlite: Session = Depends(get_performance_db),
    db_mysql: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Create a new performance review with auto-calculated metrics. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can create performance reviews.")

    # Calculate metrics
    metrics = compute_performance_metrics(
        employee_id=review_data.employee_id,
        start_date_str=review_data.start_date,
        end_date_str=review_data.end_date,
        db_sqlite=db_sqlite,
        db_mysql=db_mysql
    )

    new_review = PerformanceReview(
        employee_id=review_data.employee_id,
        reviewer_id=current_user.get("employee_id") or current_user.get("username") or "Admin",
        review_period=review_data.review_period,
        task_completion_rate=metrics["task_completion_rate"],
        okr_completion_rate=metrics["okr_completion_rate"],
        timesheet_hours=metrics["timesheet_hours"],
        calculated_score=metrics["calculated_score"],
        allocated_rating=review_data.allocated_rating or metrics["recommended_rating"],
        manager_feedback=review_data.manager_feedback,
        status=review_data.status or "Draft",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db_sqlite.add(new_review)
    db_sqlite.commit()
    db_sqlite.refresh(new_review)
    return new_review


@router.get("/reviews", response_model=List[ReviewResponseSchema])
def list_reviews(
    employee_id: Optional[str] = Query(None),
    review_period: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """List performance reviews. Employees only see their own"""
    query = db.query(PerformanceReview)
    
    if current_user["role"] == "Employee":
        query = query.filter(PerformanceReview.employee_id == current_user.get("employee_id"))
    else:
        if employee_id:
            query = query.filter(PerformanceReview.employee_id == employee_id)
            
    if review_period:
        query = query.filter(PerformanceReview.review_period == review_period)
    if status:
        query = query.filter(PerformanceReview.status == status)
        
    return query.all()


@router.get("/reviews/{id}", response_model=ReviewResponseSchema)
def get_review(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get a specific performance review"""
    review = db.query(PerformanceReview).filter(PerformanceReview.id == id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Performance review not found.")
        
    if current_user["role"] == "Employee" and current_user.get("employee_id") != review.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
        
    return review


@router.put("/reviews/{id}", response_model=ReviewResponseSchema)
def update_review(
    id: int,
    payload: ReviewUpdateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Update a performance review. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can update reviews.")

    review = db.query(PerformanceReview).filter(PerformanceReview.id == id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Performance review not found.")

    update_data = payload.dict(exclude_unset=True)
    for key, val in update_data.items():
        setattr(review, key, val)
        
    review.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(review)
    return review


@router.delete("/reviews/{id}", status_code=status.HTTP_200_OK)
def delete_review(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete a performance review. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can delete reviews.")

    review = db.query(PerformanceReview).filter(PerformanceReview.id == id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Performance review not found.")

    db.delete(review)
    db.commit()
    return {"message": "Performance review deleted successfully", "id": id}


class StatusTransitionSchema(BaseModel):
    status: str # e.g. Submitted, Approved, Rejected

@router.put("/reviews/{id}/status", response_model=ReviewResponseSchema)
def transition_review_status(
    id: int,
    payload: StatusTransitionSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Transition status of performance review (submit, approve, reject). Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can transition review statuses.")

    review = db.query(PerformanceReview).filter(PerformanceReview.id == id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Performance review not found.")

    review.status = payload.status
    review.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(review)
    return review


@router.get("/reports/ratings", response_model=dict)
def get_ratings_report(
    review_period: Optional[str] = Query(None, description="Review period like Q1 2026"),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get performance rating distribution report. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins and SuperAdmins can view ratings reports.")

    query = db.query(PerformanceReview)
    if review_period:
        query = query.filter(PerformanceReview.review_period == review_period)
    
    reviews = query.all()
    total_reviews = len(reviews)
    
    rating_counts = {}
    score_sum = 0.0
    for r in reviews:
        score_sum += r.calculated_score or 0.0
        rating = r.allocated_rating or "Unrated"
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
        
    avg_score = round(score_sum / total_reviews, 2) if total_reviews > 0 else 0.0
    
    # Calculate percentages
    rating_percentages = {}
    for rating, count in rating_counts.items():
        rating_percentages[rating] = {
            "count": count,
            "percentage": round((count / total_reviews) * 100.0, 2) if total_reviews > 0 else 0.0
        }

    return {
        "review_period": review_period or "All Periods",
        "total_reviews": total_reviews,
        "average_calculated_score": avg_score,
        "rating_distribution": rating_percentages,
        "generated_at": datetime.utcnow()
    }


# ==================== KPI MANAGEMENT ENDPOINTS ====================

class KPICreateSchema(BaseModel):
    employee_id: str
    kpi_name: str
    description: Optional[str] = None
    target_value: float
    actual_value: float = 0.0
    unit: Optional[str] = None
    weight: Optional[float] = 1.0
    review_period: str
    category: Optional[str] = None

class KPIUpdateSchema(BaseModel):
    kpi_name: Optional[str] = None
    description: Optional[str] = None
    target_value: Optional[float] = None
    actual_value: Optional[float] = None
    unit: Optional[str] = None
    weight: Optional[float] = None
    category: Optional[str] = None

class KPIResponseSchema(BaseModel):
    id: int
    employee_id: str
    kpi_name: str
    description: Optional[str]
    target_value: float
    actual_value: float
    unit: Optional[str]
    weight: float
    achievement_rate: float
    review_period: str
    category: Optional[str]
    created_by: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


@router.post("/kpi", response_model=KPIResponseSchema, status_code=status.HTTP_201_CREATED)
def create_kpi(
    payload: KPICreateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Create a KPI record for an employee."""
    if current_user["role"] == "Employee" and current_user.get("employee_id") != payload.employee_id:
        raise HTTPException(status_code=403, detail="Employees can only create KPIs for themselves.")
    denom = payload.target_value if payload.target_value != 0 else 1.0
    achievement_rate = round(min(max((payload.actual_value / denom) * 100.0, 0.0), 100.0), 2)
    kpi = KPIRecord(
        employee_id=payload.employee_id,
        kpi_name=payload.kpi_name,
        description=payload.description,
        target_value=payload.target_value,
        actual_value=payload.actual_value,
        unit=payload.unit,
        weight=payload.weight if payload.weight is not None else 1.0,
        achievement_rate=achievement_rate,
        review_period=payload.review_period,
        category=payload.category,
        created_by=current_user.get("employee_id") or current_user.get("username"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(kpi)
    db.commit()
    db.refresh(kpi)
    return kpi


@router.get("/kpi", response_model=List[KPIResponseSchema])
def list_kpis(
    employee_id: Optional[str] = Query(None),
    review_period: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """List KPI records. Employees only see their own."""
    query = db.query(KPIRecord)
    if current_user["role"] == "Employee":
        query = query.filter(KPIRecord.employee_id == current_user.get("employee_id"))
    else:
        if employee_id:
            query = query.filter(KPIRecord.employee_id == employee_id)
    if review_period:
        query = query.filter(KPIRecord.review_period == review_period)
    if category:
        query = query.filter(KPIRecord.category == category)
    return query.all()


@router.get("/kpi/{id}", response_model=KPIResponseSchema)
def get_kpi(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get a specific KPI record."""
    kpi = db.query(KPIRecord).filter(KPIRecord.id == id).first()
    if not kpi:
        raise HTTPException(status_code=404, detail="KPI record not found.")
    if current_user["role"] == "Employee" and current_user.get("employee_id") != kpi.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return kpi


@router.put("/kpi/{id}", response_model=KPIResponseSchema)
def update_kpi(
    id: int,
    payload: KPIUpdateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Update a KPI record and auto-recalculate achievement_rate."""
    kpi = db.query(KPIRecord).filter(KPIRecord.id == id).first()
    if not kpi:
        raise HTTPException(status_code=404, detail="KPI record not found.")
    if current_user["role"] == "Employee" and current_user.get("employee_id") != kpi.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    for key, val in payload.dict(exclude_unset=True).items():
        setattr(kpi, key, val)
    denom = kpi.target_value if kpi.target_value != 0 else 1.0
    kpi.achievement_rate = round(min(max((kpi.actual_value / denom) * 100.0, 0.0), 100.0), 2)
    kpi.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(kpi)
    return kpi


@router.delete("/kpi/{id}", status_code=status.HTTP_200_OK)
def delete_kpi(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete a KPI record. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can delete KPI records.")
    kpi = db.query(KPIRecord).filter(KPIRecord.id == id).first()
    if not kpi:
        raise HTTPException(status_code=404, detail="KPI record not found.")
    db.delete(kpi)
    db.commit()
    return {"message": "KPI record deleted successfully", "id": id}


# ==================== BULK CALCULATE & REVIEW HISTORY ====================

class BulkCalculateSchema(BaseModel):
    employee_ids: List[str]
    start_date: str
    end_date: str


@router.post("/reviews/bulk-calculate", response_model=dict)
def bulk_calculate_performance(
    payload: BulkCalculateSchema,
    db_sqlite: Session = Depends(get_performance_db),
    db_mysql: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Bulk-calculate performance metrics for multiple employees. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can bulk-calculate.")
    results = []
    errors = []
    for emp_id in payload.employee_ids:
        try:
            metrics = compute_performance_metrics(
                employee_id=emp_id,
                start_date_str=payload.start_date,
                end_date_str=payload.end_date,
                db_sqlite=db_sqlite,
                db_mysql=db_mysql
            )
            results.append({"employee_id": emp_id, **metrics})
        except Exception as e:
            errors.append({"employee_id": emp_id, "error": str(e)})
    return {
        "total_requested": len(payload.employee_ids),
        "total_calculated": len(results),
        "total_errors": len(errors),
        "results": results,
        "errors": errors,
        "calculated_at": datetime.utcnow()
    }


@router.get("/reviews/history/{employee_id}", response_model=List[ReviewResponseSchema])
def get_employee_review_history(
    employee_id: str,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Full performance review history for an employee, newest first."""
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return (
        db.query(PerformanceReview)
        .filter(PerformanceReview.employee_id == employee_id)
        .order_by(PerformanceReview.created_at.desc())
        .all()
    )
