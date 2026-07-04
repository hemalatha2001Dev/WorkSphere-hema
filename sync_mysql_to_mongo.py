import sys
import os
from sqlalchemy.orm import Session

# Ensure we can import the app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.mysql import SessionLocal
from app.db.mongo import get_mongo_client
from app.core.config import settings

# Import all models
from app.models.mysql_models import (
    User, Project, ProjectFile, ProjectChat, Task, TaskActivity, TaskChat
)
from app.models.performance_models import (
    SalaryConfiguration, Objective, KeyResult, PerformanceReview, 
    PayrollRecord, KPIRecord, RatingAllocation
)
from app.models.attendance import AttendanceLog # assuming this exists based on compass

def row_to_dict(row):
    """Converts a SQLAlchemy model instance into a dictionary."""
    d = {}
    for column in row.__table__.columns:
        d[column.name] = getattr(row, column.name)
    return d

def sync_data():
    db = SessionLocal()
    mongo_client = get_mongo_client()
    
    if not mongo_client:
        print("❌ MongoDB is not available. Please check the connection.")
        return
        
    mongo_db = mongo_client[settings.MONGO_DB_NAME]
    
    # Map of MongoDB Collection Name -> SQLAlchemy Model
    sync_map = {
        "users": User,
        "projects": Project,
        "project_files": ProjectFile,
        "project_chats": ProjectChat,
        "tasks": Task,
        "task_activities": TaskActivity,
        "task_chats": TaskChat,
        "salary_configurations": SalaryConfiguration,
        "objectives": Objective,
        "key_results": KeyResult,
        "performance_reviews": PerformanceReview,
        "payroll_records": PayrollRecord,
        "kpi_records": KPIRecord,
        "rating_allocations": RatingAllocation,
    }
    
    print("🚀 Starting MySQL to MongoDB Migration...")
    
    for collection_name, model in sync_map.items():
        print(f"\nSyncing table: {collection_name}...")
        collection = mongo_db[collection_name]
        
        try:
            # Query all records using the ORM (this automatically handles JSON fields)
            records = db.query(model).all()
            
            if not records:
                print(f"  - No data found in MySQL table '{collection_name}'. Skipping.")
                continue
                
            inserted_count = 0
            skipped_count = 0
            
            for record in records:
                doc = row_to_dict(record)
                
                # Check if it already exists in Mongo so we don't create duplicates
                query_filter = {}
                if 'id' in doc:
                    query_filter['id'] = doc['id']
                elif 'employee_id' in doc:
                    query_filter['employee_id'] = doc['employee_id']
                
                if query_filter:
                    existing = collection.find_one(query_filter)
                    if existing:
                        skipped_count += 1
                        continue # Skip to avoid overwriting newer Mongo data
                
                # Insert the document
                collection.insert_one(doc)
                inserted_count += 1
                
            print(f"  - ✅ Successfully synced {inserted_count} new records to MongoDB.")
            if skipped_count > 0:
                print(f"  - ⏭️ Skipped {skipped_count} records (already exist in MongoDB).")
            
        except Exception as e:
            print(f"  - ❌ Error syncing {collection_name}: {e}")

    db.close()
    print("\n🎉 Migration completed successfully!")

if __name__ == "__main__":
    sync_data()
