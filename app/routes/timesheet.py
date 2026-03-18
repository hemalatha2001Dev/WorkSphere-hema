from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel
import pandas as pd
import os
from io import BytesIO

from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.core.security import get_current_user
from app.core.config import settings
from app.models.mysql_models import User

router = APIRouter(prefix="/api/v1/timesheet", tags=["Timesheet"])

# ==================== SCHEMAS ====================

class TimesheetEntrySchema(BaseModel):
    date: str  # YYYY-MM-DD format
    project: str
    priority: str  # High, Medium, Low
    task: str
    status: str  # Not Started, In Progress, Completed
    hours_spent: int
    notes: Optional[str] = None


class TimesheetCreateSchema(BaseModel):
    employee_id: str
    entries: List[TimesheetEntrySchema]
    month: str  # YYYY-MM format


class TimesheetResponseSchema(BaseModel):
    id: int
    employee_id: str
    month: str
    entries: List[dict]
    created_at: datetime
    updated_at: Optional[datetime]


# ==================== MYSQL MODEL ====================

# Add this to your mysql_models.py
"""
class Timesheet(Base):
    __tablename__ = "timesheets"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String(100), nullable=False, index=True)
    month = Column(String(7), nullable=False)  # YYYY-MM
    entries = Column(JSON, nullable=True)  # Array of timesheet entries
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_deleted = Column(Boolean, default=False)
    
    __table_args__ = (
        Index('idx_employee_month', 'employee_id', 'month'),
        Index('idx_employee', 'employee_id'),
    )
"""

# ==================== CREATE TIMESHEET ====================

@router.post("/create", response_model=dict)
def create_timesheet(
    timesheet_data: TimesheetCreateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Create or update timesheet entries for an employee"""
    try:
        employee_id = timesheet_data.employee_id
        month = timesheet_data.month
        
        # Validate employee exists
        employee = db.query(User).filter(User.employee_id == employee_id).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Permission check: Employee can only create their own, Admin can create for their dept
        if current_user["role"] == "Employee":
            if current_user.get("employee_id") != employee_id:
                raise HTTPException(status_code=403, detail="Can only create your own timesheet")
        elif current_user["role"] == "Admin":
            if employee.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="Can only create for your department")
        
        # Prepare timesheet data
        entries = [entry.dict() for entry in timesheet_data.entries]
        
        timesheet_doc = {
            "employee_id": employee_id,
            "month": month,
            "entries": entries,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        # Create in MongoDB
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            result = mongo_db["timesheets"].update_one(
                {"employee_id": employee_id, "month": month},
                {"$set": timesheet_doc},
                upsert=True
            )
        
        return {
            "message": "Timesheet created/updated successfully",
            "employee_id": employee_id,
            "month": month,
            "total_entries": len(entries)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating timesheet: {str(e)}")


# ==================== GET TIMESHEET ====================

@router.get("/view", response_model=dict)
def get_timesheet(
    employee_id: str = Query(...),
    month: str = Query(...),  # YYYY-MM format
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    View timesheet for an employee.
    Admin can view own department employees, SuperAdmin can view anyone
    """
    try:
        # Permission check
        employee = db.query(User).filter(User.employee_id == employee_id).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        if current_user["role"] == "Admin":
            if employee.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="Can only view own department")
        elif current_user["role"] == "Employee":
            if current_user.get("employee_id") != employee_id:
                raise HTTPException(status_code=403, detail="Can only view your own timesheet")
        
        # Get from MongoDB first
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            timesheet = mongo_db["timesheets"].find_one(
                {"employee_id": employee_id, "month": month},
                {"_id": 0}
            )
            
            if timesheet:
                return {
                    "source": "mongo",
                    "employee_id": employee_id,
                    "employee_name": f"{employee.firstname} {employee.lastname}",
                    "department": employee.department,
                    "month": month,
                    "entries": timesheet.get("entries", []),
                    "total_hours": sum(e.get("hours_spent", 0) for e in timesheet.get("entries", [])),
                    "total_entries": len(timesheet.get("entries", []))
                }
        
        return {
            "source": "mysql",
            "employee_id": employee_id,
            "employee_name": f"{employee.firstname} {employee.lastname}",
            "department": employee.department,
            "month": month,
            "entries": [],
            "total_hours": 0,
            "total_entries": 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching timesheet: {str(e)}")


# ==================== LIST ALL TIMESHEETS ====================

@router.get("/list", response_model=dict)
def list_timesheets(
    month: Optional[str] = Query(None),  # YYYY-MM format, if None shows current month
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    List all timesheets.
    Admin sees own department, SuperAdmin sees all
    """
    try:
        if not month:
            month = datetime.now().strftime("%Y-%m")
        
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            
            query = {"month": month}
            
            # Apply role-based filtering
            if current_user["role"] == "Admin":
                department = current_user.get("department")
                # Get all employees in this department
                employees = db.query(User).filter(User.department == department).all()
                employee_ids = [e.employee_id for e in employees]
                query["employee_id"] = {"$in": employee_ids}
            
            timesheets = list(mongo_db["timesheets"].find(query, {"_id": 0}))
            
            # Enrich with employee names
            enriched = []
            for ts in timesheets:
                employee = db.query(User).filter(User.employee_id == ts["employee_id"]).first()
                if employee:
                    enriched.append({
                        "employee_id": ts["employee_id"],
                        "employee_name": f"{employee.firstname} {employee.lastname}",
                        "department": employee.department,
                        "month": ts["month"],
                        "total_hours": sum(e.get("hours_spent", 0) for e in ts.get("entries", [])),
                        "total_entries": len(ts.get("entries", []))
                    })
            
            return {
                "source": "mongo",
                "month": month,
                "total_timesheets": len(enriched),
                "timesheets": enriched
            }
        
        return {
            "source": "mysql",
            "month": month,
            "total_timesheets": 0,
            "timesheets": []
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing timesheets: {str(e)}")


# ==================== EXPORT TO EXCEL ====================

@router.get("/export")
def export_timesheet_to_excel(
    employee_id: str = Query(...),
    month: str = Query(...),  # YYYY-MM format
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Export timesheet to Excel format.
    Admin can export own department, SuperAdmin can export anyone
    """
    try:
        # Permission check
        employee = db.query(User).filter(User.employee_id == employee_id).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        if current_user["role"] == "Admin":
            if employee.department != current_user.get("department"):
                raise HTTPException(status_code=403, detail="Can only export own department")
        elif current_user["role"] == "Employee":
            if current_user.get("employee_id") != employee_id:
                raise HTTPException(status_code=403, detail="Can only export your own timesheet")
        
        # Get timesheet from MongoDB
        mongo_client = get_mongo_client()
        if mongo_client:
            mongo_db = mongo_client[settings.MONGO_DB_NAME]
            timesheet = mongo_db["timesheets"].find_one(
                {"employee_id": employee_id, "month": month},
                {"_id": 0}
            )
        else:
            timesheet = None
        
        if not timesheet or not timesheet.get("entries"):
            raise HTTPException(status_code=404, detail="No timesheet entries found")
        
        # Prepare data for Excel
        df_data = []
        for entry in timesheet["entries"]:
            df_data.append({
                "Date": entry.get("date"),
                "Project": entry.get("project"),
                "Priority": entry.get("priority"),
                "Task": entry.get("task"),
                "Status": entry.get("status"),
                "Hours Spent": entry.get("hours_spent"),
                "Notes": entry.get("notes", "")
            })
        
        df = pd.DataFrame(df_data)
        
        # Add summary row
        total_hours = df["Hours Spent"].sum()
        summary_df = pd.DataFrame([{
            "Date": "TOTAL",
            "Project": "",
            "Priority": "",
            "Task": "",
            "Status": "",
            "Hours Spent": total_hours,
            "Notes": ""
        }])
        
        df = pd.concat([df, summary_df], ignore_index=True)
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Timesheet', index=False)
            
            # Format the workbook
            workbook = writer.book
            worksheet = writer.sheets['Timesheet']
            
            # Add header information
            worksheet.insert_rows(1, 3)
            worksheet['A1'] = f"Employee: {employee.firstname} {employee.lastname}"
            worksheet['A2'] = f"Employee ID: {employee_id}"
            worksheet['A3'] = f"Month: {month}"
        
        output.seek(0)
        
        # Return file
        filename = f"timesheet_{employee_id}_{month}.xlsx"
        return FileResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exporting timesheet: {str(e)}")