import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes.router import api_router
from app.db.performance_db import engine as perf_engine, Base as PerfBase

# Import all models so SQLAlchemy knows about them before create_all()
import app.models.mysql_models  # noqa: F401
import app.models.performance_models  # noqa: F401

app = FastAPI(
    title="WorkSphere Backend",
    description="Backend API for WorkSphere Platform",
    version="1.0.0"
)

# CORS configuration to allow local frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure uploads directory exists and mount it to serve static files
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Include consolidated routes
app.include_router(api_router)

@app.on_event("startup")
def create_new_tables():
    """Create any new MySQL tables that don't exist yet (safe — does NOT drop existing tables)."""
    PerfBase.metadata.create_all(bind=perf_engine)

@app.get("/")
def root():
    return {
        "status": "online",
        "message": "WorkSphere Backend is running"
    }
