from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from app.db.performance_db import get_performance_db
from app.models.performance_models import Objective, KeyResult
from app.models.mysql_models import User
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/okr", tags=["OKR"])

# ==================== SCHEMAS ====================

class KeyResultCreateSchema(BaseModel):
    title: str
    target_value: float
    current_value: float = 0.0
    unit: Optional[str] = "Percentage"
    weight: Optional[float] = 1.0

class KeyResultUpdateSchema(BaseModel):
    title: Optional[str] = None
    target_value: Optional[float] = None
    current_value: Optional[float] = None
    unit: Optional[str] = None
    weight: Optional[float] = None

class KeyResultResponseSchema(BaseModel):
    id: int
    objective_id: int
    title: str
    target_value: float
    current_value: float
    unit: Optional[str]
    weight: Optional[float]
    progress: float
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ObjectiveCreateSchema(BaseModel):
    title: str
    description: Optional[str] = None
    employee_id: str
    department: Optional[str] = None
    start_date: Optional[str] = None
    target_date: Optional[str] = None
    status: Optional[str] = "Pending"
    key_results: List[KeyResultCreateSchema] = []

class ObjectiveUpdateSchema(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    department: Optional[str] = None
    start_date: Optional[str] = None
    target_date: Optional[str] = None
    status: Optional[str] = None

class ObjectiveResponseSchema(BaseModel):
    id: int
    title: str
    description: Optional[str]
    employee_id: str
    department: Optional[str]
    start_date: Optional[str]
    target_date: Optional[str]
    progress: float
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    key_results: List[KeyResultResponseSchema]

    class Config:
        from_attributes = True

# ==================== HELPER FUNCTION ====================

def update_objective_progress(objective: Objective, db: Session):
    """Recalculate parent objective progress based on key results"""
    krs = objective.key_results
    if not krs:
        objective.progress = 0.0
    else:
        total_weight = 0.0
        weighted_progress = 0.0
        for kr in krs:
            # kr progress is (current/target) * 100
            denom = kr.target_value if kr.target_value != 0 else 1.0
            kr.progress = min(max((kr.current_value / denom) * 100.0, 0.0), 100.0)
            
            weight = kr.weight if kr.weight is not None else 1.0
            total_weight += weight
            weighted_progress += kr.progress * weight
        
        if total_weight > 0:
            objective.progress = round(weighted_progress / total_weight, 2)
        else:
            objective.progress = 0.0
            
    objective.updated_at = datetime.utcnow()
    db.commit()

# ==================== OBJECTIVE ENDPOINTS ====================

@router.post("/objectives", response_model=ObjectiveResponseSchema, status_code=status.HTTP_201_CREATED)
def create_objective(
    obj_data: ObjectiveCreateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Create objective and its associated key results"""
    # Permission check: Employee can only create their own OKRs
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj_data.employee_id:
        raise HTTPException(status_code=403, detail="Employees can only create OKRs for themselves.")

    # Verify employee exists
    employee = db.query(User).filter(User.employee_id == obj_data.employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID '{obj_data.employee_id}' not found."
        )

    # Check for duplicate objective title for the same employee
    existing_obj = db.query(Objective).filter(
        Objective.employee_id == obj_data.employee_id,
        Objective.title == obj_data.title
    ).first()
    if existing_obj:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An objective with the title '{obj_data.title}' already exists for employee '{obj_data.employee_id}'."
        )

    new_obj = Objective(
        title=obj_data.title,
        description=obj_data.description,
        employee_id=obj_data.employee_id,
        department=obj_data.department or current_user.get("department"),
        start_date=obj_data.start_date,
        target_date=obj_data.target_date,
        status=obj_data.status or "Pending",
        progress=0.0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(new_obj)
    db.commit()
    db.refresh(new_obj)

    # Add key results if any
    for kr_data in obj_data.key_results:
        denom = kr_data.target_value if kr_data.target_value != 0 else 1.0
        kr_progress = min(max((kr_data.current_value / denom) * 100.0, 0.0), 100.0)
        
        kr = KeyResult(
            objective_id=new_obj.id,
            title=kr_data.title,
            target_value=kr_data.target_value,
            current_value=kr_data.current_value,
            unit=kr_data.unit,
            weight=kr_data.weight,
            progress=kr_progress,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(kr)
    
    db.commit()
    db.refresh(new_obj)
    
    # Recalculate progress to update parent objective
    update_objective_progress(new_obj, db)
    db.refresh(new_obj)
    return new_obj


@router.get("/objectives", response_model=List[ObjectiveResponseSchema])
def list_objectives(
    employee_id: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """List objectives with filters and role-based permissions"""
    query = db.query(Objective)
    
    # Role-based restriction
    if current_user["role"] == "Employee":
        # Employees can only see their own OKRs
        query = query.filter(Objective.employee_id == current_user.get("employee_id"))
    else:
        # Admins and SuperAdmins can filter
        if employee_id:
            query = query.filter(Objective.employee_id == employee_id)
        if department:
            query = query.filter(Objective.department == department)
            
    if status:
        query = query.filter(Objective.status == status)
        
    return query.all()


@router.get("/objectives/{id}", response_model=ObjectiveResponseSchema)
def get_objective(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get a specific objective"""
    obj = db.query(Objective).filter(Objective.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found")
        
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied to this objective.")
        
    return obj


@router.put("/objectives/{id}", response_model=ObjectiveResponseSchema)
def update_objective(
    id: int,
    obj_data: ObjectiveUpdateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Update general objective fields"""
    obj = db.query(Objective).filter(Objective.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found")
        
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied to update this objective.")

    update_payload = obj_data.dict(exclude_unset=True)
    for key, val in update_payload.items():
        setattr(obj, key, val)
        
    obj.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/objectives/{id}", status_code=status.HTTP_200_OK)
def delete_objective(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete objective and its key results"""
    obj = db.query(Objective).filter(Objective.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found")
        
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied to delete this objective.")
        
    db.delete(obj)
    db.commit()
    return {"message": "Objective deleted successfully", "id": id}


# ==================== KEY RESULT ENDPOINTS ====================

@router.post("/key-results", response_model=KeyResultResponseSchema, status_code=status.HTTP_201_CREATED)
def create_key_result(
    kr_data: KeyResultCreateSchema,
    objective_id: int = Query(...),
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Create a new Key Result and update objective progress"""
    obj = db.query(Objective).filter(Objective.id == objective_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found")
        
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    denom = kr_data.target_value if kr_data.target_value != 0 else 1.0
    kr_progress = min(max((kr_data.current_value / denom) * 100.0, 0.0), 100.0)

    new_kr = KeyResult(
        objective_id=objective_id,
        title=kr_data.title,
        target_value=kr_data.target_value,
        current_value=kr_data.current_value,
        unit=kr_data.unit,
        weight=kr_data.weight,
        progress=kr_progress,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(new_kr)
    db.commit()
    db.refresh(new_kr)

    # Recalculate parent objective
    update_objective_progress(obj, db)
    return new_kr


@router.put("/key-results/{id}", response_model=KeyResultResponseSchema)
def update_key_result(
    id: int,
    kr_data: KeyResultUpdateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Update key result progress and automatically update parent objective progress"""
    kr = db.query(KeyResult).filter(KeyResult.id == id).first()
    if not kr:
        raise HTTPException(status_code=404, detail="Key Result not found")
        
    obj = db.query(Objective).filter(Objective.id == kr.objective_id).first()
    
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    update_payload = kr_data.dict(exclude_unset=True)
    for key, val in update_payload.items():
        setattr(kr, key, val)

    # Re-calculate kr progress
    denom = kr.target_value if kr.target_value != 0 else 1.0
    kr.progress = min(max((kr.current_value / denom) * 100.0, 0.0), 100.0)
    kr.updated_at = datetime.utcnow()
    db.commit()

    # Update parent objective progress
    update_objective_progress(obj, db)
    db.refresh(kr)
    return kr


@router.delete("/key-results/{id}", status_code=status.HTTP_200_OK)
def delete_key_result(
    id: int,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete a key result and update parent objective progress"""
    kr = db.query(KeyResult).filter(KeyResult.id == id).first()
    if not kr:
        raise HTTPException(status_code=404, detail="Key Result not found")
        
    obj = db.query(Objective).filter(Objective.id == kr.objective_id).first()
    
    # Permission check
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    db.delete(kr)
    db.commit()

    # Recalculate parent objective
    update_objective_progress(obj, db)
    return {"message": "Key Result deleted successfully", "id": id}


@router.get("/reports/alignment", response_model=dict)
def get_goal_alignment_report(
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Get goal alignment and progress summary grouped by department. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins and SuperAdmins can view alignment reports.")
        
    objs = db.query(Objective).all()
    
    report = {}
    for obj in objs:
        dept = obj.department or "Unassigned"
        if dept not in report:
            report[dept] = {
                "total_objectives": 0,
                "completed_objectives": 0,
                "average_progress": 0.0,
                "progress_sum": 0.0,
                "status_counts": {}
            }
        
        dept_data = report[dept]
        dept_data["total_objectives"] += 1
        dept_data["progress_sum"] += obj.progress or 0.0
        
        status = obj.status or "Pending"
        dept_data["status_counts"][status] = dept_data["status_counts"].get(status, 0) + 1
        
        if status.lower() == "completed" or (obj.progress and obj.progress >= 100.0):
            dept_data["completed_objectives"] += 1
            
    # Calculate averages
    for dept, data in report.items():
        if data["total_objectives"] > 0:
            data["average_progress"] = round(data["progress_sum"] / data["total_objectives"], 2)
        del data["progress_sum"]
        
    return {
        "report_type": "Goal Alignment & Progress Report",
        "generated_at": datetime.utcnow(),
        "departments_summary": report
    }


# ==================== OKR ENHANCEMENTS ====================

class ObjectiveStatusUpdateSchema(BaseModel):
    status: str  # Pending, In Progress, Completed, Cancelled


@router.put("/objectives/{id}/status", response_model=ObjectiveResponseSchema)
def update_objective_status(
    id: int,
    payload: ObjectiveStatusUpdateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Quick status update for an objective."""
    obj = db.query(Objective).filter(Objective.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found.")
    if current_user["role"] == "Employee" and current_user.get("employee_id") != obj.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    allowed_statuses = ["Pending", "In Progress", "Completed", "Cancelled"]
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {allowed_statuses}")

    obj.status = payload.status
    obj.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/reports/employee/{employee_id}", response_model=dict)
def get_employee_okr_summary(
    employee_id: str,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Employee-level OKR summary: total objectives, average progress, status breakdown."""
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    objectives = db.query(Objective).filter(Objective.employee_id == employee_id).all()
    total = len(objectives)

    if total == 0:
        return {
            "employee_id": employee_id,
            "total_objectives": 0,
            "average_progress": 0.0,
            "status_breakdown": {},
            "total_key_results": 0,
            "generated_at": datetime.utcnow()
        }

    avg_progress = round(sum(o.progress or 0.0 for o in objectives) / total, 2)
    status_breakdown = {}
    total_krs = 0
    for obj in objectives:
        s = obj.status or "Pending"
        status_breakdown[s] = status_breakdown.get(s, 0) + 1
        total_krs += len(obj.key_results)

    return {
        "employee_id": employee_id,
        "total_objectives": total,
        "average_progress": avg_progress,
        "status_breakdown": status_breakdown,
        "total_key_results": total_krs,
        "objectives": [
            {
                "id": o.id,
                "title": o.title,
                "progress": o.progress,
                "status": o.status,
                "target_date": o.target_date,
                "key_results_count": len(o.key_results)
            }
            for o in objectives
        ],
        "generated_at": datetime.utcnow()
    }


class BulkObjectiveCreateSchema(BaseModel):
    employee_id: str
    department: Optional[str] = None
    objectives: List[ObjectiveCreateSchema]


@router.post("/objectives/bulk-create", response_model=dict, status_code=status.HTTP_201_CREATED)
def bulk_create_objectives(
    payload: BulkObjectiveCreateSchema,
    db: Session = Depends(get_performance_db),
    current_user: dict = Depends(get_current_user)
):
    """Bulk-create multiple objectives for an employee. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can bulk-create objectives.")

    # Verify employee exists
    employee = db.query(User).filter(User.employee_id == payload.employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID '{payload.employee_id}' not found."
        )

    # Check for duplicate objective titles within the payload and database
    seen_titles = set()
    for obj_data in payload.objectives:
        if obj_data.title in seen_titles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate objective title '{obj_data.title}' found in the bulk request payload."
            )
        seen_titles.add(obj_data.title)

        existing_obj = db.query(Objective).filter(
            Objective.employee_id == payload.employee_id,
            Objective.title == obj_data.title
        ).first()
        if existing_obj:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"An objective with the title '{obj_data.title}' already exists for employee '{payload.employee_id}'."
            )

    created = []
    for obj_data in payload.objectives:
        new_obj = Objective(
            title=obj_data.title,
            description=obj_data.description,
            employee_id=payload.employee_id,
            department=payload.department or obj_data.department,
            start_date=obj_data.start_date,
            target_date=obj_data.target_date,
            status=obj_data.status or "Pending",
            progress=0.0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_obj)
        db.commit()
        db.refresh(new_obj)

        for kr_data in obj_data.key_results:
            denom = kr_data.target_value if kr_data.target_value != 0 else 1.0
            kr_progress = min(max((kr_data.current_value / denom) * 100.0, 0.0), 100.0)
            kr = KeyResult(
                objective_id=new_obj.id,
                title=kr_data.title,
                target_value=kr_data.target_value,
                current_value=kr_data.current_value,
                unit=kr_data.unit,
                weight=kr_data.weight,
                progress=kr_progress,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(kr)
        db.commit()
        db.refresh(new_obj)
        update_objective_progress(new_obj, db)
        created.append({"id": new_obj.id, "title": new_obj.title, "progress": new_obj.progress})

    return {
        "employee_id": payload.employee_id,
        "total_created": len(created),
        "objectives": created,
        "created_at": datetime.utcnow()
    }
