from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
import logging
import time

from app.db.mysql import get_mysql_session
from app.db.mongo import get_mongo_client
from app.core.config import settings
from app.models.mysql_models import User
from app.models.performance_models import SalaryConfiguration, PayrollRecord
from app.core.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/payroll", tags=["Payroll"])

# ==================== SCHEMAS ====================

class SalaryConfigCreateSchema(BaseModel):
    employee_id: str
    base_salary: float = 0.0
    allowances: float = 0.0
    deductions: float = 0.0
    bank_name: Optional[str] = None
    account_number: Optional[str] = None

class SalaryConfigResponseSchema(SalaryConfigCreateSchema):
    id: int
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class PayrollGenerateSchema(BaseModel):
    employee_id: str
    month: str # YYYY-MM format

class PayrollRecordUpdateSchema(BaseModel):
    base_salary: Optional[float] = None
    allowances: Optional[float] = None
    deductions: Optional[float] = None
    status: Optional[str] = None # e.g. Draft, Processed, Paid

class PayrollRecordResponseSchema(BaseModel):
    id: int
    employee_id: str
    month: str
    present_days: int
    absent_days: int
    total_working_days: int
    base_salary: float
    allowances: float
    deductions: float
    net_salary: float
    status: str
    payment_date: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# ==================== SALARY CONFIGURATION ENDPOINTS ====================

@router.post("/configurations", response_model=SalaryConfigResponseSchema, status_code=status.HTTP_201_CREATED)
def create_or_update_salary_configuration(
    config_data: SalaryConfigCreateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Create or update salary configuration. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can manage salary configurations.")

    mongo_client = get_mongo_client()
    mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None

    # Try MySQL first
    result_config = None
    mysql_success = False
    mysql_id = None
    try:
        existing_config = db.query(SalaryConfiguration).filter(
            SalaryConfiguration.employee_id == config_data.employee_id
        ).first()
        if existing_config:
            existing_config.base_salary = config_data.base_salary
            existing_config.allowances = config_data.allowances
            existing_config.deductions = config_data.deductions
            existing_config.bank_name = config_data.bank_name
            existing_config.account_number = config_data.account_number
            existing_config.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing_config)
            result_config = existing_config
        else:
            new_config = SalaryConfiguration(
                employee_id=config_data.employee_id,
                base_salary=config_data.base_salary,
                allowances=config_data.allowances,
                deductions=config_data.deductions,
                bank_name=config_data.bank_name,
                account_number=config_data.account_number,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(new_config)
            db.commit()
            db.refresh(new_config)
            result_config = new_config
        mysql_success = True
        mysql_id = result_config.id
    except Exception as e:
        logger.error(f"MySQL salary config failed: {e}")
        db.rollback()

    # Write to MongoDB
    mongo_success = False
    if mongo_db is not None:
        try:
            mongo_doc = {
                "id": mysql_id if mysql_success else -int(time.time()),
                "employee_id": config_data.employee_id,
                "base_salary": config_data.base_salary,
                "allowances": config_data.allowances,
                "deductions": config_data.deductions,
                "bank_name": config_data.bank_name,
                "account_number": config_data.account_number,
                "is_synced": mysql_success,
                "created_at": result_config.created_at if mysql_success else datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            mongo_db["salary_configurations"].update_one(
                {"employee_id": config_data.employee_id},
                {"$set": mongo_doc},
                upsert=True
            )
            mongo_success = True
            if not mysql_success:
                result_config = SalaryConfiguration(**{k: v for k, v in mongo_doc.items() if k not in ["_id", "is_synced"]})
        except Exception as e:
            logger.error(f"MongoDB salary config failed: {e}")

    if result_config is None:
        raise HTTPException(status_code=500, detail="Database failure. Could not save salary configuration.")

    return result_config


@router.get("/configurations/{employee_id}", response_model=SalaryConfigResponseSchema)
def get_salary_configuration(
    employee_id: str,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get salary configuration. Employees can only get their own"""
    if current_user["role"] == "Employee" and current_user.get("employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    config = db.query(SalaryConfiguration).filter(
        SalaryConfiguration.employee_id == employee_id
    ).first()
    
    if not config:
        raise HTTPException(status_code=404, detail="Salary configuration not found for this employee.")
        
    return config


# ==================== PAYROLL RECORDS ENDPOINTS ====================

@router.post("/records/generate", response_model=PayrollRecordResponseSchema, status_code=status.HTTP_201_CREATED)
def generate_payroll_record(
    payload: PayrollGenerateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Generate payroll record for an employee and month. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can generate payroll records.")

    mongo_client = get_mongo_client()
    mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None

    # Check if payroll record already exists for this month
    existing_record = None
    try:
        existing_record = db.query(PayrollRecord).filter(
            PayrollRecord.employee_id == payload.employee_id,
            PayrollRecord.month == payload.month
        ).first()
    except Exception as e:
        logger.warning(f"MySQL check existing payroll failed: {e}")
        db.rollback()
        if mongo_db is not None:
            try:
                existing_record = mongo_db["payroll_records"].find_one({
                    "employee_id": payload.employee_id,
                    "month": payload.month
                })
            except Exception:
                pass

    if existing_record:
        raise HTTPException(
            status_code=400, 
            detail=f"Payroll record already exists for employee {payload.employee_id} and month {payload.month}."
        )

    # Fetch salary configuration
    salary_config = None
    try:
        salary_config = db.query(SalaryConfiguration).filter(
            SalaryConfiguration.employee_id == payload.employee_id
        ).first()
    except Exception as e:
        logger.warning(f"MySQL fetch salary config failed: {e}")
        db.rollback()

    if not salary_config and mongo_db is not None:
        try:
            salary_config_doc = mongo_db["salary_configurations"].find_one({"employee_id": payload.employee_id})
            if salary_config_doc:
                salary_config = SalaryConfiguration(**{k: v for k, v in salary_config_doc.items() if k not in ["_id", "is_synced"]})
        except Exception:
            pass

    if not salary_config:
        raise HTTPException(
            status_code=404, 
            detail=f"No salary configuration found for employee {payload.employee_id}. Please configure salary first."
        )

    # Fetch attendance from MongoDB timesheets
    present_days = 0
    total_working_days = 22 # Defaulting to 22 working days in a month
    
    try:
        if mongo_db is not None:
            timesheet = mongo_db["timesheets"].find_one({
                "employee_id": payload.employee_id,
                "month": payload.month
            })
            if timesheet and "entries" in timesheet:
                # Count unique dates as present days
                unique_dates = set()
                for entry in timesheet["entries"]:
                    date_str = entry.get("date")
                    if date_str:
                        unique_dates.add(date_str)
                present_days = len(unique_dates)
    except Exception as e:
        print(f"Warning: Could not fetch attendance data: {e}")

    # Ensure present days doesn't exceed total working days for calculation
    effective_present_days = min(present_days, total_working_days)
    absent_days = total_working_days - effective_present_days
    
    # Prorated base salary calculation based on attendance
    prorated_base_salary = round((salary_config.base_salary / total_working_days) * effective_present_days, 2)

    # Net Salary calculation
    net_salary = prorated_base_salary + salary_config.allowances - salary_config.deductions

    # Write to MySQL
    mysql_success = False
    mysql_id = None
    new_record = None
    try:
        new_record = PayrollRecord(
            employee_id=payload.employee_id,
            month=payload.month,
            present_days=effective_present_days,
            absent_days=absent_days,
            total_working_days=total_working_days,
            base_salary=prorated_base_salary,
            allowances=salary_config.allowances,
            deductions=salary_config.deductions,
            net_salary=net_salary,
            status="Draft",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        mysql_success = True
        mysql_id = new_record.id
    except Exception as e:
        logger.error(f"MySQL create payroll record failed: {e}")
        db.rollback()

    # Write to MongoDB
    mongo_success = False
    if mongo_db is not None:
        try:
            pay_doc = {
                "id": mysql_id if mysql_success else -int(time.time()),
                "employee_id": payload.employee_id,
                "month": payload.month,
                "present_days": effective_present_days,
                "absent_days": absent_days,
                "total_working_days": total_working_days,
                "base_salary": prorated_base_salary,
                "allowances": salary_config.allowances,
                "deductions": salary_config.deductions,
                "net_salary": net_salary,
                "status": "Draft",
                "is_synced": mysql_success,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            mongo_db["payroll_records"].update_one(
                {"employee_id": payload.employee_id, "month": payload.month},
                {"$set": pay_doc},
                upsert=True
            )
            mongo_success = True
            if not mysql_success:
                new_record = PayrollRecord(**{k: v for k, v in pay_doc.items() if k not in ["_id", "is_synced"]})
        except Exception as e:
            logger.error(f"MongoDB save payroll record failed: {e}")

    if not mysql_success and not mongo_success:
        raise HTTPException(status_code=500, detail="Database failure. Could not save payroll record.")

    return new_record


@router.get("/records", response_model=List[PayrollRecordResponseSchema])
def list_payroll_records(
    employee_id: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """List payroll records. Employees can only list their own"""
    mongo_client = get_mongo_client()
    mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None

    # Try MongoDB first
    if mongo_db is not None:
        try:
            q = {}
            if current_user["role"] == "Employee":
                q["employee_id"] = current_user.get("employee_id")
            else:
                if employee_id: q["employee_id"] = employee_id
            if month: q["month"] = month
            if status: q["status"] = status
            results = list(mongo_db["payroll_records"].find(q))
            for r in results: r.pop("_id", None)
            return results
        except Exception as e:
            logger.warning(f"MongoDB list_payroll failed: {e}. Falling back to MySQL.")

    # Fallback to MySQL
    try:
        query = db.query(PayrollRecord)
        if current_user["role"] == "Employee":
            query = query.filter(PayrollRecord.employee_id == current_user.get("employee_id"))
        else:
            if employee_id:
                query = query.filter(PayrollRecord.employee_id == employee_id)
        if month:
            query = query.filter(PayrollRecord.month == month)
        if status:
            query = query.filter(PayrollRecord.status == status)
        return query.all()
    except Exception as e:
        logger.error(f"MySQL list_payroll failed: {e}")
        raise HTTPException(status_code=500, detail="Database failure. Could not fetch payroll records.")


@router.get("/records/{id}", response_model=PayrollRecordResponseSchema)
def get_payroll_record(
    id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get specific payroll record. Employees can only get their own"""
    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")
        
    if current_user["role"] == "Employee" and current_user.get("employee_id") != record.employee_id:
        raise HTTPException(status_code=403, detail="Access denied.")
        
    return record


@router.put("/records/{id}", response_model=PayrollRecordResponseSchema)
def update_payroll_record(
    id: int,
    payload: PayrollRecordUpdateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Update payroll record fields. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can update payroll records.")

    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")

    update_data = payload.dict(exclude_unset=True)
    
    for key, val in update_data.items():
        setattr(record, key, val)

    # Recalculate net salary if financial values change
    record.net_salary = record.base_salary + record.allowances - record.deductions
    
    if payload.status == "Paid":
        record.payment_date = datetime.utcnow()
        
    record.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(record)
    return record


@router.delete("/records/{id}", status_code=status.HTTP_200_OK)
def delete_payroll_record(
    id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Delete payroll record. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can delete payroll records.")

    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")

    db.delete(record)
    db.commit()
    return {"message": "Payroll record deleted successfully", "id": id}


@router.get("/records/{id}/payslip", response_model=dict)
def get_payslip(
    id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Generate and return a formatted payslip with employee name, department, bank and salary info"""
    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")

    # Access control: Employee can only see their own payslip
    if current_user["role"] == "Employee" and current_user.get("employee_id") != record.employee_id:
        raise HTTPException(status_code=403, detail="Access denied to this payslip.")

    # Fetch employee info from MySQL
    employee = db.query(User).filter(User.employee_id == record.employee_id).first()
    employee_name = f"{employee.firstname} {employee.lastname}" if employee else "Unknown Employee"
    department = employee.department if employee else "Unknown"
    position = employee.position if employee else "Unknown"

    # Fetch bank details from Salary Configuration in MySQL
    salary_config = db.query(SalaryConfiguration).filter(
        SalaryConfiguration.employee_id == record.employee_id
    ).first()

    bank_name = salary_config.bank_name if salary_config else "Not Configured"
    account_number = salary_config.account_number if salary_config else "Not Configured"

    return {
        "payslip_id": record.id,
        "employee_id": record.employee_id,
        "employee_name": employee_name,
        "department": department,
        "position": position,
        "month": record.month,
        "bank_name": bank_name,
        "account_number": account_number,
        "attendance": {
            "total_working_days": record.total_working_days,
            "present_days": record.present_days,
            "absent_days": record.absent_days
        },
        "financial_details": {
            "base_salary": record.base_salary,
            "allowances": record.allowances,
            "deductions": record.deductions,
            "net_salary": record.net_salary
        },
        "status": record.status,
        "payment_date": record.payment_date,
        "generated_at": datetime.utcnow()
    }


@router.get("/reports/summary", response_model=dict)
def get_salary_report(
    month: str = Query(..., description="Month in YYYY-MM format"),
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Get salary payout report for a specific month. Admin/SuperAdmin only"""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins and SuperAdmins can view salary reports.")

    records = db.query(PayrollRecord).filter(PayrollRecord.month == month).all()

    total_base = sum(r.base_salary for r in records)
    total_allowances = sum(r.allowances for r in records)
    total_deductions = sum(r.deductions for r in records)
    total_net = sum(r.net_salary for r in records)
    total_records = len(records)

    status_counts = {}
    for r in records:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    return {
        "month": month,
        "total_records": total_records,
        "total_base_salary": round(total_base, 2),
        "total_allowances": round(total_allowances, 2),
        "total_deductions": round(total_deductions, 2),
        "total_net_salary": round(total_net, 2),
        "status_distribution": status_counts,
        "generated_at": datetime.utcnow()
    }


# ==================== ENHANCED PAYROLL ENDPOINTS ====================

@router.get("/configurations", response_model=List[SalaryConfigResponseSchema])
def list_all_salary_configurations(
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """List all employee salary configurations. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can list all salary configurations.")
    return db.query(SalaryConfiguration).all()


class BulkPayrollGenerateSchema(BaseModel):
    month: str  # YYYY-MM


@router.post("/records/bulk-generate", response_model=dict, status_code=status.HTTP_201_CREATED)
def bulk_generate_payroll(
    payload: BulkPayrollGenerateSchema,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """
    Generate payroll records for ALL employees who have salary configurations for a given month.
    Skips employees who already have a record for that month. Admin/SuperAdmin only.
    """
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can bulk-generate payroll.")

    all_configs = db.query(SalaryConfiguration).all()
    generated = []
    skipped = []

    for config in all_configs:
        existing = db.query(PayrollRecord).filter(
            PayrollRecord.employee_id == config.employee_id,
            PayrollRecord.month == payload.month
        ).first()

        if existing:
            skipped.append({"employee_id": config.employee_id, "reason": "Record already exists"})
            continue

        # Fetch attendance from MongoDB timesheets
        present_days = 0
        total_working_days = 22 # Defaulting to 22 working days in a month
        
        try:
            mongo_client = get_mongo_client()
            if mongo_client:
                mongo_db = mongo_client[settings.MONGO_DB_NAME]
                timesheet = mongo_db["timesheets"].find_one({
                    "employee_id": config.employee_id,
                    "month": payload.month
                })
                if timesheet and "entries" in timesheet:
                    unique_dates = set()
                    for entry in timesheet["entries"]:
                        date_str = entry.get("date")
                        if date_str:
                            unique_dates.add(date_str)
                    present_days = len(unique_dates)
        except Exception as e:
            print(f"Warning: Could not fetch attendance data: {e}")

        effective_present_days = min(present_days, total_working_days)
        absent_days = total_working_days - effective_present_days
        prorated_base_salary = round((config.base_salary / total_working_days) * effective_present_days, 2)

        net_salary = prorated_base_salary + config.allowances - config.deductions
        record = PayrollRecord(
            employee_id=config.employee_id,
            month=payload.month,
            present_days=effective_present_days,
            absent_days=absent_days,
            total_working_days=total_working_days,
            base_salary=prorated_base_salary,
            allowances=config.allowances,
            deductions=config.deductions,
            net_salary=net_salary,
            status="Draft",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(record)
        generated.append(config.employee_id)

    db.commit()
    return {
        "month": payload.month,
        "total_generated": len(generated),
        "total_skipped": len(skipped),
        "generated_for": generated,
        "skipped": skipped,
        "generated_at": datetime.utcnow()
    }


@router.put("/records/{id}/process", response_model=PayrollRecordResponseSchema)
def process_payroll_record(
    id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Mark a payroll record as Processed (Draft -> Processed). Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can process payroll records.")
    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")
    if record.status != "Draft":
        raise HTTPException(status_code=400, detail=f"Record is already in '{record.status}' status. Can only process Draft records.")
    record.status = "Processed"
    record.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(record)
    return record


@router.put("/records/{id}/pay", response_model=PayrollRecordResponseSchema)
def pay_payroll_record(
    id: int,
    db: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Mark a payroll record as Paid (Processed -> Paid) and set payment_date. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins or SuperAdmins can mark payroll as paid.")
    record = db.query(PayrollRecord).filter(PayrollRecord.id == id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Payroll record not found.")
    if record.status != "Processed":
        raise HTTPException(status_code=400, detail=f"Record must be in 'Processed' status before marking as Paid. Current status: {record.status}")
    record.status = "Paid"
    record.payment_date = datetime.utcnow()
    record.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(record)
    return record


@router.get("/reports/export", response_model=dict)
def export_payroll_report(
    month: str = Query(..., description="Month in YYYY-MM format"),
    db: Session = Depends(get_mysql_session),
    db_mysql: Session = Depends(get_mysql_session),
    current_user: dict = Depends(get_current_user)
):
    """Export full payroll report for a month including employee names. Admin/SuperAdmin only."""
    if current_user["role"] not in ["Admin", "SuperAdmin"]:
        raise HTTPException(status_code=403, detail="Only Admins and SuperAdmins can export payroll reports.")

    records = db.query(PayrollRecord).filter(PayrollRecord.month == month).all()
    export_rows = []
    for r in records:
        employee = db_mysql.query(User).filter(User.employee_id == r.employee_id).first()
        export_rows.append({
            "payroll_id": r.id,
            "employee_id": r.employee_id,
            "employee_name": f"{employee.firstname} {employee.lastname}" if employee else "Unknown",
            "department": employee.department if employee else "Unknown",
            "position": employee.position if employee else "Unknown",
            "month": r.month,
            "present_days": r.present_days,
            "absent_days": r.absent_days,
            "total_working_days": r.total_working_days,
            "base_salary": r.base_salary,
            "allowances": r.allowances,
            "deductions": r.deductions,
            "net_salary": r.net_salary,
            "status": r.status,
            "payment_date": str(r.payment_date) if r.payment_date else None
        })

    total_net = sum(r.net_salary for r in records)
    return {
        "month": month,
        "total_records": len(records),
        "total_net_payout": round(total_net, 2),
        "records": export_rows,
        "exported_at": datetime.utcnow()
    }
