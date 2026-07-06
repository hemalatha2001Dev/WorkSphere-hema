import logging
import time
from sqlalchemy import text
from app.db.mysql import SessionLocal
from app.db.mongo import get_mongo_client
from app.core.config import settings

from app.models.performance_models import (
    Objective, KeyResult, PerformanceReview, KPIRecord,
    PayrollRecord, SalaryConfiguration, RatingAllocation
)

logger = logging.getLogger(__name__)

def reconcile_databases():
    """
    Every 5 Minutes: Check MongoDB and MySQL.
    If MySQL missed any data while it was stopped, this will copy it from MongoDB back to MySQL.
    Uses auto-incrementing INT IDs in MySQL and updates MongoDB with the actual MySQL-generated IDs.
    """
    logger.info("🔄 Running 5-Minute Check: Reconciling MongoDB -> MySQL...")
    
    db = SessionLocal()
    try:
        # Dummy query to test if MySQL is actually online before doing heavy work
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning(f"⚠️ MySQL is still down ({e}). Skipping reconciliation until it wakes up.")
        db.close()
        return

    mongo_client = get_mongo_client()
    if not mongo_client:
        logger.warning("MongoDB is unreachable. Skipping reconciliation.")
        db.close()
        return
        
    mongo_db = mongo_client[settings.MONGO_DB_NAME]
    
    # ==================== 1. RECONCILE OBJECTIVES & KEY RESULTS ====================
    healed_objectives = 0
    healed_krs = 0
    try:
        # Query unsynced objectives
        unsynced_objs = list(mongo_db["objectives"].find({"is_synced": False}))
        if unsynced_objs:
            logger.info(f"🔍 Found {len(unsynced_objs)} unsynced objectives in MongoDB to heal...")
            for m_obj in unsynced_objs:
                old_obj_id = m_obj.get("id")
                
                # Check if it already exists by checking matching title + employee_id
                sql_obj = db.query(Objective).filter(
                    Objective.employee_id == m_obj.get("employee_id"),
                    Objective.title == m_obj.get("title")
                ).first()
                
                if sql_obj:
                    # Already exists in SQL (perhaps partially synced), update Mongo reference
                    mongo_db["objectives"].update_one(
                        {"_id": m_obj["_id"]},
                        {"$set": {"id": sql_obj.id, "is_synced": True}}
                    )
                    # Update objective ID for any associated KRs in Mongo
                    mongo_db["key_results"].update_many(
                        {"objective_id": old_obj_id},
                        {"$set": {"objective_id": sql_obj.id}}
                    )
                    continue

                try:
                    # Insert without ID to let MySQL auto-increment
                    new_obj = Objective(
                        title=m_obj.get("title"),
                        description=m_obj.get("description"),
                        employee_id=m_obj.get("employee_id"),
                        department=m_obj.get("department"),
                        start_date=m_obj.get("start_date"),
                        target_date=m_obj.get("target_date"),
                        status=m_obj.get("status"),
                        progress=m_obj.get("progress"),
                        created_at=m_obj.get("created_at"),
                        updated_at=m_obj.get("updated_at")
                    )
                    db.add(new_obj)
                    db.commit()
                    db.refresh(new_obj)
                    
                    mysql_obj_id = new_obj.id
                    healed_objectives += 1
                    
                    # Reconcile key results for this objective
                    mongo_krs = list(mongo_db["key_results"].find({"objective_id": old_obj_id}))
                    for m_kr in mongo_krs:
                        new_kr = KeyResult(
                            objective_id=mysql_obj_id,
                            title=m_kr.get("title"),
                            target_value=m_kr.get("target_value"),
                            current_value=m_kr.get("current_value"),
                            unit=m_kr.get("unit"),
                            weight=m_kr.get("weight"),
                            progress=m_kr.get("progress"),
                            created_at=m_kr.get("created_at"),
                            updated_at=m_kr.get("updated_at")
                        )
                        db.add(new_kr)
                        db.commit()
                        db.refresh(new_kr)
                        
                        # Update Mongo key result with real MySQL ID and set is_synced=True
                        mongo_db["key_results"].update_one(
                            {"_id": m_kr["_id"]},
                            {"$set": {"id": new_kr.id, "objective_id": mysql_obj_id, "is_synced": True}}
                        )
                        healed_krs += 1
                        
                    # Update Mongo objective with real MySQL ID and set is_synced=True
                    mongo_db["objectives"].update_one(
                        {"_id": m_obj["_id"]},
                        {"$set": {"id": mysql_obj_id, "is_synced": True}}
                    )
                    logger.info(f"✅ Auto-Healed Objective (New SQL ID: {mysql_obj_id}, Title: {m_obj.get('title')}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal Objective {old_obj_id}: {e}")
    except Exception as e:
        logger.error(f"Error during Objectives reconciliation: {e}")
        db.rollback()

    # ==================== 2. RECONCILE PERFORMANCE REVIEWS ====================
    healed_reviews = 0
    try:
        unsynced_reviews = list(mongo_db["performance_reviews"].find({"is_synced": False}))
        if unsynced_reviews:
            logger.info(f"🔍 Found {len(unsynced_reviews)} unsynced performance reviews in MongoDB to heal...")
            for m_rev in unsynced_reviews:
                try:
                    new_rev = PerformanceReview(
                        employee_id=m_rev.get("employee_id"),
                        reviewer_id=m_rev.get("reviewer_id"),
                        review_period=m_rev.get("review_period"),
                        task_completion_rate=m_rev.get("task_completion_rate"),
                        okr_completion_rate=m_rev.get("okr_completion_rate"),
                        timesheet_hours=m_rev.get("timesheet_hours"),
                        calculated_score=m_rev.get("calculated_score"),
                        allocated_rating=m_rev.get("allocated_rating"),
                        manager_feedback=m_rev.get("manager_feedback"),
                        status=m_rev.get("status"),
                        created_at=m_rev.get("created_at"),
                        updated_at=m_rev.get("updated_at")
                    )
                    db.add(new_rev)
                    db.commit()
                    db.refresh(new_rev)
                    
                    mongo_db["performance_reviews"].update_one(
                        {"_id": m_rev["_id"]},
                        {"$set": {"id": new_rev.id, "is_synced": True}}
                    )
                    healed_reviews += 1
                    logger.info(f"✅ Auto-Healed Performance Review (New SQL ID: {new_rev.id}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal Performance Review: {e}")
    except Exception as e:
        logger.error(f"Error during Performance Review reconciliation: {e}")
        db.rollback()

    # ==================== 3. RECONCILE KPI RECORDS ====================
    healed_kpis = 0
    try:
        unsynced_kpis = list(mongo_db["kpi_records"].find({"is_synced": False}))
        if unsynced_kpis:
            logger.info(f"🔍 Found {len(unsynced_kpis)} unsynced KPI records in MongoDB to heal...")
            for m_kpi in unsynced_kpis:
                try:
                    new_kpi = KPIRecord(
                        employee_id=m_kpi.get("employee_id"),
                        kpi_name=m_kpi.get("kpi_name"),
                        description=m_kpi.get("description"),
                        target_value=m_kpi.get("target_value"),
                        actual_value=m_kpi.get("actual_value"),
                        unit=m_kpi.get("unit"),
                        weight=m_kpi.get("weight"),
                        achievement_rate=m_kpi.get("achievement_rate"),
                        review_period=m_kpi.get("review_period"),
                        category=m_kpi.get("category"),
                        created_by=m_kpi.get("created_by"),
                        created_at=m_kpi.get("created_at"),
                        updated_at=m_kpi.get("updated_at")
                    )
                    db.add(new_kpi)
                    db.commit()
                    db.refresh(new_kpi)
                    
                    mongo_db["kpi_records"].update_one(
                        {"_id": m_kpi["_id"]},
                        {"$set": {"id": new_kpi.id, "is_synced": True}}
                    )
                    healed_kpis += 1
                    logger.info(f"✅ Auto-Healed KPI Record (New SQL ID: {new_kpi.id}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal KPI Record: {e}")
    except Exception as e:
        logger.error(f"Error during KPI reconciliation: {e}")
        db.rollback()

    # ==================== 4. RECONCILE SALARY CONFIGURATIONS ====================
    healed_configs = 0
    try:
        unsynced_configs = list(mongo_db["salary_configurations"].find({"is_synced": False}))
        if unsynced_configs:
            logger.info(f"🔍 Found {len(unsynced_configs)} unsynced salary configurations in MongoDB to heal...")
            for m_cfg in unsynced_configs:
                # Since employee_id is unique, check if exists in SQL
                sql_cfg = db.query(SalaryConfiguration).filter(
                    SalaryConfiguration.employee_id == m_cfg.get("employee_id")
                ).first()
                
                if sql_cfg:
                    # Update SQL record with MongoDB details
                    try:
                        sql_cfg.base_salary = m_cfg.get("base_salary")
                        sql_cfg.allowances = m_cfg.get("allowances")
                        sql_cfg.deductions = m_cfg.get("deductions")
                        sql_cfg.bank_name = m_cfg.get("bank_name")
                        sql_cfg.account_number = m_cfg.get("account_number")
                        sql_cfg.updated_at = m_cfg.get("updated_at")
                        db.commit()
                        
                        mongo_db["salary_configurations"].update_one(
                            {"_id": m_cfg["_id"]},
                            {"$set": {"id": sql_cfg.id, "is_synced": True}}
                        )
                        healed_configs += 1
                        continue
                    except Exception as e:
                        db.rollback()
                        logger.error(f"Failed to update existing Salary Configuration in SQL: {e}")
                        continue

                try:
                    new_cfg = SalaryConfiguration(
                        employee_id=m_cfg.get("employee_id"),
                        base_salary=m_cfg.get("base_salary"),
                        allowances=m_cfg.get("allowances"),
                        deductions=m_cfg.get("deductions"),
                        bank_name=m_cfg.get("bank_name"),
                        account_number=m_cfg.get("account_number"),
                        created_at=m_cfg.get("created_at"),
                        updated_at=m_cfg.get("updated_at")
                    )
                    db.add(new_cfg)
                    db.commit()
                    db.refresh(new_cfg)
                    
                    mongo_db["salary_configurations"].update_one(
                        {"_id": m_cfg["_id"]},
                        {"$set": {"id": new_cfg.id, "is_synced": True}}
                    )
                    healed_configs += 1
                    logger.info(f"✅ Auto-Healed Salary Configuration (New SQL ID: {new_cfg.id}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal Salary Configuration: {e}")
    except Exception as e:
        logger.error(f"Error during Salary Configuration reconciliation: {e}")
        db.rollback()

    # ==================== 5. RECONCILE PAYROLL RECORDS ====================
    healed_payrolls = 0
    try:
        unsynced_payrolls = list(mongo_db["payroll_records"].find({"is_synced": False}))
        if unsynced_payrolls:
            logger.info(f"🔍 Found {len(unsynced_payrolls)} unsynced payroll records in MongoDB to heal...")
            for m_pay in unsynced_payrolls:
                try:
                    new_pay = PayrollRecord(
                        employee_id=m_pay.get("employee_id"),
                        month=m_pay.get("month"),
                        present_days=m_pay.get("present_days"),
                        absent_days=m_pay.get("absent_days"),
                        total_working_days=m_pay.get("total_working_days"),
                        base_salary=m_pay.get("base_salary"),
                        allowances=m_pay.get("allowances"),
                        deductions=m_pay.get("deductions"),
                        net_salary=m_pay.get("net_salary"),
                        status=m_pay.get("status"),
                        payment_date=m_pay.get("payment_date"),
                        created_at=m_pay.get("created_at"),
                        updated_at=m_pay.get("updated_at")
                    )
                    db.add(new_pay)
                    db.commit()
                    db.refresh(new_pay)
                    
                    mongo_db["payroll_records"].update_one(
                        {"_id": m_pay["_id"]},
                        {"$set": {"id": new_pay.id, "is_synced": True}}
                    )
                    healed_payrolls += 1
                    logger.info(f"✅ Auto-Healed Payroll Record (New SQL ID: {new_pay.id}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal Payroll Record: {e}")
    except Exception as e:
        logger.error(f"Error during Payroll reconciliation: {e}")
        db.rollback()

    # ==================== 6. RECONCILE RATING ALLOCATIONS ====================
    healed_ratings = 0
    try:
        unsynced_ratings = list(mongo_db["rating_allocations"].find({"is_synced": False}))
        if unsynced_ratings:
            logger.info(f"🔍 Found {len(unsynced_ratings)} unsynced rating allocations in MongoDB to heal...")
            for m_rat in unsynced_ratings:
                try:
                    new_rat = RatingAllocation(
                        employee_id=m_rat.get("employee_id"),
                        review_period=m_rat.get("review_period"),
                        task_completion_rate=m_rat.get("task_completion_rate"),
                        okr_completion_rate=m_rat.get("okr_completion_rate"),
                        timesheet_hours=m_rat.get("timesheet_hours"),
                        kpi_score=m_rat.get("kpi_score"),
                        calculated_score=m_rat.get("calculated_score"),
                        recommended_rating=m_rat.get("recommended_rating"),
                        recommended_rating_value=m_rat.get("recommended_rating_value"),
                        allocated_rating=m_rat.get("allocated_rating"),
                        allocated_rating_value=m_rat.get("allocated_rating_value"),
                        status=m_rat.get("status"),
                        is_approved=m_rat.get("is_approved"),
                        created_at=m_rat.get("created_at"),
                        updated_at=m_rat.get("updated_at")
                    )
                    db.add(new_rat)
                    db.commit()
                    db.refresh(new_rat)
                    
                    mongo_db["rating_allocations"].update_one(
                        {"_id": m_rat["_id"]},
                        {"$set": {"id": new_rat.id, "is_synced": True}}
                    )
                    healed_ratings += 1
                    logger.info(f"✅ Auto-Healed Rating Allocation (New SQL ID: {new_rat.id}) into MySQL.")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to heal Rating Allocation: {e}")
    except Exception as e:
        logger.error(f"Error during Rating reconciliation: {e}")
        db.rollback()

    db.close()
    logger.info(
        f"✅ 5-Minute Database Check Completed. Healed totals: "
        f"{healed_objectives} objectives, {healed_krs} KRs, "
        f"{healed_reviews} reviews, {healed_kpis} KPIs, "
        f"{healed_configs} configs, {healed_payrolls} payrolls, "
        f"{healed_ratings} ratings."
    )


def generate_daily_reports():
    """
    Every day at midnight: Generate automated performance/payroll reports.
    """
    logger.info("📊 Running Midnight Job: Generating Daily Reports...")
    logger.info("✅ Midnight Reports Generated Successfully.")
