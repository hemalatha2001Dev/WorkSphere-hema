# app/core/utils.py
from datetime import datetime, timezone
from typing import Optional, Dict
from sqlalchemy.orm import Session
from pymongo.database import Database
from fastapi import HTTPException
from app.core.filters import apply_role_based_filter_mongo_projects, apply_role_based_filter_projects
from app.models.mysql_models import Project
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# app/core/utils.py (Final Update)
# ... (same imports as above)

def get_alert_count(
    db: Session,
    mongo_db: Optional[Database],
    current_user: Dict,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """Calculate the number of projects nearing deadline (80% timeline reached)."""
    try:
        current_time = datetime.now(timezone.utc)
        logger.debug(f"Current time for alert calculation: {current_time.isoformat()}")

        if mongo_db is not None:
            # MongoDB logic
            mongo_query = {"is_deleted": {"$ne": True}, "project_status": {"$ne": "Completed"}}
            if start_date and end_date:
                mongo_query["created_at"] = {"$gte": start_date, "$lte": end_date}
                logger.debug(f"MongoDB date filter: {start_date} to {end_date}")

            mongo_query = apply_role_based_filter_mongo_projects(mongo_query, current_user)
            projects = list(mongo_db["projects"].find(mongo_query, {"_id": 0}))
            logger.debug(f"Found {len(projects)} projects in MongoDB")

            alert_count = 0
            for project in projects:
                deadline = project.get("deadline")
                if not deadline:
                    logger.debug(f"Project {project.get('id')} skipped: no deadline")
                    continue

                try:
                    if isinstance(deadline, str):
                        deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                    else:
                        deadline_dt = deadline
                    if not deadline_dt.tzinfo:
                        deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)

                    # Use start_date if available, else created_at
                    project_start_str = project.get("start_date") or project.get("created_at", current_time)
                    if isinstance(project_start_str, str):
                        project_start = datetime.fromisoformat(project_start_str.replace("Z", "+00:00"))
                    else:
                        project_start = project_start_str
                    if not project_start.tzinfo:
                        project_start = project_start.replace(tzinfo=timezone.utc)

                    total_duration = deadline_dt - project_start
                    total_seconds = total_duration.total_seconds()
                    # Remove invalid duration check to match /alerts/upcoming-deadlines
                    eighty_percent_time = project_start + (total_duration * 0.8)
                    if current_time >= eighty_percent_time:
                        alert_count += 1
                        logger.debug(
                            f"Project {project.get('id')} triggers alert: "
                            f"80% time={eighty_percent_time.isoformat()}, current={current_time.isoformat()}"
                        )
                except Exception as e:
                    logger.error(f"Project {project.get('id')} date parsing error: {str(e)}")
                    continue

            return alert_count

        # MySQL logic (unchanged)
        query = db.query(Project).filter(
            Project.is_deleted == False, Project.project_status != "Completed"
        )
        if start_date and end_date:
            query = query.filter(Project.created_at.between(start_date, end_date))
            logger.debug(f"MySQL date filter: {start_date} to {end_date}")

        query = apply_role_based_filter_projects(query, current_user)
        projects = query.all()
        logger.debug(f"Found {len(projects)} projects in MySQL")

        alert_count = 0
        for project in projects:
            if not project.deadline:
                logger.debug(f"Project {project.id} skipped: no deadline")
                continue

            project_start = project.created_at or current_time
            if not project_start.tzinfo:
                project_start = project_start.replace(tzinfo=timezone.utc)
            if not project.deadline.tzinfo:
                project.deadline = project.deadline.replace(tzinfo=timezone.utc)

            total_duration = project.deadline - project_start
            total_seconds = total_duration.total_seconds()
            if total_seconds <= 0:
                logger.debug(f"Project {project.id} skipped: invalid duration")
                continue

            eighty_percent_time = project_start + (total_duration * 0.8)
            if current_time >= eighty_percent_time:
                alert_count += 1
                logger.debug(
                    f"Project {project.id} triggers alert: "
                    f"80% time={eighty_percent_time.isoformat()}, current={current_time.isoformat()}"
                )

        return alert_count

    except Exception as e:
        logger.error(f"Error calculating alert count: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error calculating alert count: {str(e)}")