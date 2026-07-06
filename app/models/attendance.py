from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from app.db.mysql import Base


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "punch_time", name="uq_attendance_user_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)  # Employee ID from Biometric
    punch_time = Column(DateTime, index=True)
    punch_type = Column(String(50))  # IN, OUT, etc.
    device_ip = Column(String(50))
