from fastapi import APIRouter
from app.routes import auth, common, dashboard, employee, project ,tasks ,timesheet

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(employee.router)
router.include_router(common.router) 
router.include_router(project.router)
router.include_router(tasks.router)
router.include_router(timesheet.router)