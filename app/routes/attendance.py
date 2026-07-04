from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, date
from typing import Optional
from collections import defaultdict

from app.db.mysql import get_db
from app.db.mongo import get_mongo_client
from app.models.attendance import AttendanceLog
from app.core.config import settings

router = APIRouter(prefix="/api/v1/attendance", tags=["Attendance"])


# ==================== HELPER ====================

def get_mongo_attendance(mongo_db, query: dict, sort_field="punch_time", limit=None):
    """Fetch attendance logs from MongoDB as a list of dicts."""
    if mongo_db is None:
        return []
    cursor = mongo_db["attendance_logs"].find(query, {"_id": 0}).sort(sort_field, -1)
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def compute_daily_hours(records: list) -> float:
    """
    Given a list of punch records (dicts or ORM objects), determine IN/OUT
    from the device_name field (e.g. '4THFLOOR IN', '2nd Floor OUT').
    Falls back to first-punch = IN, last-punch = OUT if device names are ambiguous.
    Returns total hours as a float (e.g. 7.5).
    """
    def get_val(r, key):
        return getattr(r, key, None) if not isinstance(r, dict) else r.get(key)

    def parse_time(ptime):
        if isinstance(ptime, str):
            try:
                return datetime.fromisoformat(ptime)
            except Exception:
                return None
        return ptime

    ins, outs = [], []
    for r in records:
        device_name = str(get_val(r, "device_name") or "").upper()
        punch_type  = str(get_val(r, "punch_type") or "").upper()
        ptime = parse_time(get_val(r, "punch_time"))
        if not ptime:
            continue

        # Use device_name to determine IN vs OUT
        if "OUT" in device_name:
            outs.append(ptime)
        elif "IN" in device_name:
            ins.append(ptime)
        # Fallback: use punch_type 0=IN, 1=OUT (for other devices)
        elif punch_type in ("0", "IN"):
            ins.append(ptime)
        elif punch_type in ("1", "OUT"):
            outs.append(ptime)
        else:
            # punch_type=255 and no device_name clue — collect all
            ins.append(ptime)  # treat as IN and pair with last below

    ins.sort()
    outs.sort()

    # Pair IN → OUT sequentially
    total_seconds = 0
    i = j = 0
    while i < len(ins) and j < len(outs):
        if outs[j] > ins[i]:
            total_seconds += (outs[j] - ins[i]).total_seconds()
            i += 1
            j += 1
        else:
            j += 1

    # If no OUT records found, use first and last punch as fallback
    if total_seconds == 0 and len(ins) >= 2:
        total_seconds = (ins[-1] - ins[0]).total_seconds()

    return round(total_seconds / 3600, 2)


# ==================== 1. DAILY ATTENDANCE (with hours) ====================

@router.get("/daily")
def get_daily_attendance(
    employee_id: int,
    date_str: Optional[str] = Query(None, description="Date in YYYY-MM-DD format. Defaults to today."),
    db: Session = Depends(get_db)
):
    """
    Get all punch records for an employee on a specific date,
    along with total hours worked that day.
    """
    target_date = date.today()
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end   = datetime.combine(target_date, datetime.max.time())

    source = "mysql"
    records = []
    try:
        records = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.user_id == employee_id,
                AttendanceLog.punch_time >= day_start,
                AttendanceLog.punch_time <= day_end,
            )
            .order_by(AttendanceLog.punch_time.asc())
            .all()
        )
    except Exception as e:
        source = "mongodb (fallback)"
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        records = get_mongo_attendance(
            mongo_db,
            {"user_id": str(employee_id), "punch_time": {"$gte": day_start, "$lte": day_end}}
        )

    total_hours = compute_daily_hours(records)

    return {
        "source": source,
        "employee_id": employee_id,
        "date": str(target_date),
        "total_hours_worked": total_hours,
        "punch_count": len(records),
        "punches": [
            {
                "punch_time": str(getattr(r, "punch_time", None) or r.get("punch_time")),
                "punch_type": str(getattr(r, "punch_type", None) or r.get("punch_type")),
                "device_ip": getattr(r, "device_ip", None) or r.get("device_ip"),
            }
            for r in records
        ]
    }


# ==================== 2. MONTHLY SUMMARY (hours per day) ====================

@router.get("/monthly-summary")
def get_monthly_summary(
    employee_id: int,
    month: Optional[str] = Query(None, description="Month in YYYY-MM format. Defaults to current month."),
    db: Session = Depends(get_db)
):
    """
    Returns a day-by-day breakdown of hours worked for an employee in a given month,
    plus total hours for the month and present/absent day counts.
    """
    if month:
        try:
            month_start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM.")
    else:
        today = date.today()
        month_start = datetime(today.year, today.month, 1)

    # Compute last day of month
    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1) - timedelta(seconds=1)
    else:
        month_end = datetime(month_start.year, month_start.month + 1, 1) - timedelta(seconds=1)

    source = "mysql"
    records = []
    try:
        records = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.user_id == employee_id,
                AttendanceLog.punch_time >= month_start,
                AttendanceLog.punch_time <= month_end,
            )
            .order_by(AttendanceLog.punch_time.asc())
            .all()
        )
    except Exception as e:
        source = "mongodb (fallback)"
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        records = get_mongo_attendance(
            mongo_db,
            {"user_id": str(employee_id), "punch_time": {"$gte": month_start, "$lte": month_end}}
        )

    # Group records by day
    daily_records = defaultdict(list)
    for r in records:
        ptime = getattr(r, "punch_time", None) or r.get("punch_time")
        if isinstance(ptime, str):
            try:
                ptime = datetime.fromisoformat(ptime)
            except Exception:
                continue
        day_key = ptime.strftime("%Y-%m-%d") if ptime else None
        if day_key:
            daily_records[day_key].append(r)

    # Build per-day summary
    daily_summary = {}
    total_hours = 0.0
    for day_key, day_recs in sorted(daily_records.items()):
        hrs = compute_daily_hours(day_recs)
        daily_summary[day_key] = {
            "hours_worked": hrs,
            "punch_count": len(day_recs),
            "status": "Present" if hrs > 0 else "Punch-Only"
        }
        total_hours += hrs

    present_days = len(daily_summary)
    working_days = 22  # standard working days per month

    return {
        "source": source,
        "employee_id": employee_id,
        "month": month_start.strftime("%Y-%m"),
        "total_hours_worked": round(total_hours, 2),
        "present_days": present_days,
        "absent_days": max(0, working_days - present_days),
        "working_days_standard": working_days,
        "daily_breakdown": daily_summary
    }


# ==================== 3. TOTAL HOURS IN DATE RANGE ====================

@router.get("/total-hours")
def get_total_hours(
    employee_id: int,
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db)
):
    """
    Returns total hours worked by an employee between two dates.
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date,   "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    source = "mysql"
    records = []
    try:
        records = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.user_id == employee_id,
                AttendanceLog.punch_time >= start_dt,
                AttendanceLog.punch_time <= end_dt,
            )
            .order_by(AttendanceLog.punch_time.asc())
            .all()
        )
    except Exception as e:
        source = "mongodb (fallback)"
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        records = get_mongo_attendance(
            mongo_db,
            {"user_id": str(employee_id), "punch_time": {"$gte": start_dt, "$lte": end_dt}}
        )

    # Group by day and compute hours
    daily_records = defaultdict(list)
    for r in records:
        ptime = getattr(r, "punch_time", None) or r.get("punch_time")
        if isinstance(ptime, str):
            try:
                ptime = datetime.fromisoformat(ptime)
            except Exception:
                continue
        if ptime:
            daily_records[ptime.strftime("%Y-%m-%d")].append(r)

    total_hours = 0.0
    day_details = {}
    for day_key, day_recs in sorted(daily_records.items()):
        hrs = compute_daily_hours(day_recs)
        total_hours += hrs
        day_details[day_key] = hrs

    return {
        "source": source,
        "employee_id": employee_id,
        "start_date": start_date,
        "end_date": end_date,
        "total_hours_worked": round(total_hours, 2),
        "present_days": len(day_details),
        "daily_hours": day_details
    }


# ==================== 4. TODAY'S ATTENDANCE (punch log) ====================

@router.get("/today")
def get_today_attendance(
    employee_id: int,
    db: Session = Depends(get_db)
):
    """
    Quick check: punches and hours worked for an employee today.
    """
    today = date.today()
    day_start = datetime.combine(today, datetime.min.time())
    day_end   = datetime.combine(today, datetime.max.time())

    source = "mysql"
    records = []
    try:
        records = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.user_id == employee_id,
                AttendanceLog.punch_time >= day_start,
                AttendanceLog.punch_time <= day_end,
            )
            .order_by(AttendanceLog.punch_time.asc())
            .all()
        )
    except Exception as e:
        source = "mongodb (fallback)"
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        records = get_mongo_attendance(
            mongo_db,
            {"user_id": str(employee_id), "punch_time": {"$gte": day_start, "$lte": day_end}}
        )

    total_hours = compute_daily_hours(records)

    # Determine first IN and last OUT
    first_in = last_out = None
    for r in records:
        ptype = str(getattr(r, "punch_type", "") or r.get("punch_type", "")).upper()
        ptime = getattr(r, "punch_time", None) or r.get("punch_time")
        if "0" in ptype or "IN" in ptype:
            first_in = first_in or ptime
        elif "1" in ptype or "OUT" in ptype:
            last_out = ptime

    return {
        "source": source,
        "employee_id": employee_id,
        "date": str(today),
        "first_punch_in": str(first_in) if first_in else None,
        "last_punch_out": str(last_out) if last_out else None,
        "total_hours_worked": total_hours,
        "punch_count": len(records),
        "status": "Present" if total_hours > 0 else ("Punched-In Only" if records else "Absent")
    }


# ==================== 5. FILTER (original, improved) ====================

@router.get("/filter")
def filter_attendance(
    employee_id: int,
    mode: Optional[str] = Query(None, description="today | latest"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Generic filter: by mode (today/latest) or date range with pagination.
    """
    source = "mysql"
    records = []
    try:
        query = db.query(AttendanceLog).filter(AttendanceLog.user_id == employee_id)

        if mode == "today":
            today_start = datetime.combine(date.today(), datetime.min.time())
            query = query.filter(AttendanceLog.punch_time >= today_start)

        elif start_date and end_date:
            query = query.filter(
                AttendanceLog.punch_time >= start_date,
                AttendanceLog.punch_time <= end_date + " 23:59:59"
            )

        if mode == "latest":
            records = query.order_by(AttendanceLog.punch_time.desc()).limit(limit).all()
            return {"source": source, "data": [
                {"id": r.id, "user_id": r.user_id, "punch_time": str(r.punch_time),
                 "punch_type": r.punch_type, "device_ip": r.device_ip}
                for r in records
            ], "total": len(records)}

        total = query.count()
        offset = (page - 1) * limit
        records = query.order_by(AttendanceLog.punch_time.desc()).offset(offset).limit(limit).all()

        return {
            "source": source,
            "data": [
                {"id": r.id, "user_id": r.user_id, "punch_time": str(r.punch_time),
                 "punch_type": r.punch_type, "device_ip": r.device_ip}
                for r in records
            ],
            "total": total, "page": page, "limit": limit
        }

    except Exception as e:
        source = "mongodb (fallback)"
        mongo_client = get_mongo_client()
        mongo_db = mongo_client[settings.MONGO_DB_NAME] if mongo_client else None
        if mongo_db is None:
            raise HTTPException(status_code=503, detail="Both MySQL and MongoDB are unavailable.")

        mongo_query = {"user_id": str(employee_id)}
        if mode == "today":
            today_start = datetime.combine(date.today(), datetime.min.time())
            mongo_query["punch_time"] = {"$gte": today_start}
        elif start_date and end_date:
            mongo_query["punch_time"] = {
                "$gte": datetime.strptime(start_date, "%Y-%m-%d"),
                "$lte": datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            }

        offset = (page - 1) * limit
        total = mongo_db["attendance_logs"].count_documents(mongo_query)
        cursor = mongo_db["attendance_logs"].find(mongo_query, {"_id": 0}).sort("punch_time", -1)
        if mode != "latest":
            cursor = cursor.skip(offset)
        records = list(cursor.limit(limit))
        return {"source": source, "data": records, "total": total, "page": page, "limit": limit}
