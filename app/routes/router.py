from fastapi import APIRouter

# Import all route modules
from app.routes.auth import router as auth_router
from app.routes.employee import router as employee_router
from app.routes.common import router as common_router
from app.routes.tasks import router as tasks_router
from app.routes.timesheet import router as timesheet_router
from app.routes.project import router as project_router
from app.routes.dashboard import router as dashboard_router
from app.routes.okr import router as okr_router
from app.routes.payroll import router as payroll_router
from app.routes.performance import router as performance_router
from app.routes.rating import router as rating_router

api_router = APIRouter()

# Include all individual routers
api_router.include_router(auth_router)
api_router.include_router(employee_router)
api_router.include_router(common_router)
api_router.include_router(tasks_router)
api_router.include_router(timesheet_router)
api_router.include_router(project_router)
api_router.include_router(dashboard_router)
api_router.include_router(okr_router)
api_router.include_router(payroll_router)
api_router.include_router(performance_router)
api_router.include_router(rating_router)
