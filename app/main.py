from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.db.mysql import Base, engine
from app.models import mysql_models  
from app.routes.router import router

app = FastAPI(
    title="Management API",
    description="API with MySQL + MongoDB fallback",
    version="1.0.0"
)

# Create tables if not exists
Base.metadata.create_all(bind=engine)

# Middleware for CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

os.makedirs("uploads", exist_ok=True)

app.mount("/api/v1/uploads", StaticFiles(directory="uploads"), name="uploads_api")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")  # New route for shorter path

# Include routers
app.include_router(router)

@app.get("/")
def root():
    return {"message": "Server running with MongoDB + MySQL fallback"}