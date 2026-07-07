from zk import ZK
import logging
from datetime import datetime, timedelta
from app.db.mysql import SessionLocal
from app.db.mongo import get_mongo_client
from app.models.attendance import AttendanceLog
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEVICES = [
    {"ip": "192.168.0.75", "name": "2nd Floor OUT", "port": 4370},
    {"ip": "192.168.0.161", "name": "2nd Floor IN", "port": 4370},
    {"ip": "192.168.0.9", "name": "4THFLOOR IN", "port": 4370},
    {"ip": "192.168.0.87", "name": "4THFLOOR OUT", "port": 4370},
]


def _save_to_mysql(records, device_name):
    """
    Save records to MySQL using INSERT IGNORE with raw engine connection for maximum speed.
    Each record: {"user_id": int, "punch_time": datetime, "punch_type": str, "device_ip": str}
    """
    if not records:
        return
    
    from app.db.mysql import engine
    
    chunk_size = 500
    total_inserted = 0
    
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i + chunk_size]
        
        # Build a multi-row VALUES clause for INSERT IGNORE
        values_parts = []
        for r in chunk:
            # Escape single quotes in strings
            pt = str(r["punch_time"]).replace("'", "\\'")
            ptype = str(r["punch_type"]).replace("'", "\\'")
            dip = str(r["device_ip"]).replace("'", "\\'")
            values_parts.append(f"({r['user_id']}, '{pt}', '{ptype}', '{dip}')")
        
        values_sql = ", ".join(values_parts)
        sql = f"INSERT IGNORE INTO attendance_logs (user_id, punch_time, punch_type, device_ip) VALUES {values_sql}"
        
        try:
            with engine.connect() as conn:
                result = conn.execute(sql)
                total_inserted += result.rowcount
        except Exception as e:
            logger.error(f"MySQL chunk insert error for {device_name}: {e}")
    
    logger.info(f"MySQL: {total_inserted} new rows inserted for {device_name} (skipped {len(records) - total_inserted} duplicates)")


def _save_to_mongo(records, mongo_col, device_name):
    """
    Save records to MongoDB using bulk upsert for speed.
    """
    if not records or mongo_col is None:
        return
    
    try:
        from pymongo import UpdateOne
        ops = []
        for r in records:
            ops.append(UpdateOne(
                {"user_id": str(r["user_id"]), "punch_time": r["punch_time"]},
                {"$set": {
                    "user_id": str(r["user_id"]),
                    "punch_time": r["punch_time"],
                    "punch_type": r["punch_type"],
                    "device_ip": r["device_ip"],
                    "device_name": device_name,
                    "updated_at": datetime.now(),
                }},
                upsert=True
            ))
        
        chunk_size = 2000
        for i in range(0, len(ops), chunk_size):
            mongo_col.bulk_write(ops[i:i + chunk_size], ordered=False)
        
        logger.info(f"MongoDB: Processed {len(ops)} records for {device_name}")
    except Exception as e:
        logger.error(f"MongoDB bulk error for {device_name}: {e}")


def _fetch_from_device(device, date_filter=None):
    """
    Connect to a ZK device and fetch attendance logs.
    If date_filter is provided, only return records >= that datetime.
    Returns list of dicts.
    """
    records = []
    max_retries = 2
    
    for attempt in range(max_retries):
        zk = ZK(device["ip"], port=device["port"], timeout=15 + (attempt * 10), password=0)
        conn = None
        try:
            conn = zk.connect()
            attendance = conn.get_attendance()
            
            for log in attendance:
                if date_filter and log.timestamp < date_filter:
                    continue
                
                uid = log.user_id
                records.append({
                    "user_id": int(uid) if str(uid).isdigit() else 0,
                    "punch_time": log.timestamp,
                    "punch_type": str(log.punch),
                    "device_ip": device["ip"]
                })
            
            total = len(attendance)
            filtered = len(records)
            logger.info(f"{device['name']}: {filtered} records" + (f" (filtered from {total} total)" if date_filter else f" total"))
            break  # Success, no need to retry
            
        except Exception as e:
            logger.error(f"❌ Attempt {attempt + 1}/{max_retries} failed for {device['name']}: {e}")
            records = []  # Reset on failure
            if attempt < max_retries - 1:
                import time
                time.sleep(2)
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass  # Ignore disconnect errors — data is already fetched
    
    return records


def sync_attendance_from_devices():
    """
    FAST 5-minute sync — only processes TODAY's swipes from the biometric devices.
    This keeps the sync lightweight and fast so new swipes appear within minutes.
    """
    logger.info("⚡ Starting FAST ZK sync (today only)...")
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    mongo_client = get_mongo_client()
    mongo_db = mongo_client["worksphere"] if mongo_client else None
    mongo_col = mongo_db["attendance_logs"] if mongo_db is not None else None

    for device in DEVICES:
        records = _fetch_from_device(device, date_filter=today_start)
        if records:
            _save_to_mysql(records, device["name"])
            _save_to_mongo(records, mongo_col, device["name"])

    logger.info("⚡ FAST ZK sync completed.")


def sync_all_historical_data():
    """
    FULL historical sync — processes ALL records from ALL devices.
    Run this once to backfill, then the fast sync handles daily updates.
    """
    logger.info("📦 Starting FULL historical ZK sync (ALL records)...")

    mongo_client = get_mongo_client()
    mongo_db = mongo_client["worksphere"] if mongo_client else None
    mongo_col = mongo_db["attendance_logs"] if mongo_db is not None else None

    for device in DEVICES:
        records = _fetch_from_device(device, date_filter=None)
        if records:
            _save_to_mysql(records, device["name"])
            _save_to_mongo(records, mongo_col, device["name"])

    logger.info("📦 FULL historical sync completed.")


def start_scheduler():
    from app.utils.db_sync import reconcile_databases, generate_daily_reports
    
    scheduler = BackgroundScheduler()
    
    # 1. Every 5 minutes -> check MongoDB and MySQL
    scheduler.add_job(reconcile_databases, "interval", minutes=5)
    
    # 2. Every 5 minutes -> sync TODAY's attendance from devices (fast)
    scheduler.add_job(sync_attendance_from_devices, "interval", minutes=5)
    
    # 3. Every day at midnight -> generate reports
    scheduler.add_job(generate_daily_reports, "cron", hour=0, minute=0)
    
    # 4. Run the FIRST sync immediately on startup so data is available right away
    scheduler.add_job(sync_attendance_from_devices, "date", run_date=datetime.now() + timedelta(seconds=10))
    
    scheduler.start()
    logger.info("✅ APScheduler started: DB Sync (5m), Attendance (5m), Reports (Midnight).")
