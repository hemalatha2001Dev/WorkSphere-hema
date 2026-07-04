from zk import ZK
import logging
from datetime import datetime
from app.db.mysql import SessionLocal
from app.db.mongo import get_mongo_client
from app.models.attendance import AttendanceLog
from sqlalchemy.exc import IntegrityError
from apscheduler.schedulers.background import BackgroundScheduler

from app.utils.db_sync import reconcile_databases, generate_daily_reports

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEVICES = [
    {"ip": "192.168.0.75", "name": "2nd Floor OUT", "port": 4370},
    {"ip": "192.168.0.161", "name": "2nd Floor IN", "port": 4370},
    {"ip": "192.168.0.9", "name": "4THFLOOR IN", "port": 4370},
    {"ip": "192.168.0.87", "name": "4THFLOOR OUT", "port": 4370},
]


def sync_attendance_from_devices():
    logger.info("Starting ZK device sync...")

    db = SessionLocal()
    mongo_client = get_mongo_client()
    mongo_db = mongo_client["worksphere"] if mongo_client else None
    mongo_col = mongo_db["attendance_logs"] if mongo_db is not None else None

    for device in DEVICES:
        zk = ZK(device["ip"], port=device["port"], timeout=5, password=0)
        conn = None
        try:
            conn = zk.connect()
            logger.info(f"✅ Connected to device {device['name']} ({device['ip']})")

            attendance = conn.get_attendance()
            logger.info(f"Found {len(attendance)} logs on {device['name']}.")

            inserted_count = 0

            for log in attendance:
                user_id = log.user_id
                punch_time = log.timestamp
                punch_type = str(log.punch)

                # Try saving to MySQL
                mysql_log = AttendanceLog(
                    user_id=int(user_id) if user_id.isdigit() else 0,
                    punch_time=punch_time,
                    punch_type=punch_type,
                    device_ip=device["ip"],
                )

                try:
                    db.add(mysql_log)
                    db.commit()
                    inserted_count += 1
                except IntegrityError:
                    db.rollback()  # Skip duplicate
                except Exception as e:
                    db.rollback()
                    logger.error(f"MySQL error: {e}")

                # Save to MongoDB
                if mongo_col is not None:
                    try:
                        mongo_col.update_one(
                            {"user_id": user_id, "punch_time": punch_time},
                            {
                                "$set": {
                                    "user_id": user_id,
                                    "punch_time": punch_time,
                                    "punch_type": punch_type,
                                    "device_ip": device["ip"],
                                    "device_name": device["name"],
                                    "updated_at": datetime.now(),
                                }
                            },
                            upsert=True,
                        )
                    except Exception as e:
                        logger.error(f"MongoDB error: {e}")

            logger.info(
                f"Finished processing device {device['name']}. Inserted new: {inserted_count}"
            )

        except Exception as e:
            logger.error(f"❌ Error connecting/syncing {device['name']}: {e}")
        finally:
            if conn:
                conn.disconnect()

    db.close()
    logger.info("ZK device sync completed.")


def start_scheduler():
    scheduler = BackgroundScheduler()
    
    # 1. Every 5 minutes -> check MongoDB and MySQL
    scheduler.add_job(reconcile_databases, "interval", minutes=5)
    
    # 2. Every hour -> sync attendance devices
    scheduler.add_job(sync_attendance_from_devices, "interval", hours=1)
    
    # 3. Every day at midnight -> generate reports
    scheduler.add_job(generate_daily_reports, "cron", hour=0, minute=0)
    
    scheduler.start()
    logger.info("✅ APScheduler started: DB Sync (5m), Attendance (1h), Reports (Midnight).")
