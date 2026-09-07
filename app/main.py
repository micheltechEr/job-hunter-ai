import os
import sys
import asyncio
import logging

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import init_db
from app.api.jobs import router as jobs_router
from app.api.resumes import router as resumes_router
from app.api.applications import router as applications_router
from app.api.gmail import router as gmail_router
from app.services.scheduler import start_scheduler, shutdown_scheduler
from app.services.rate_limiter import APIRateLimitMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("job_hunter.main")

app = FastAPI(
    title="Job Hunter AI",
    description="IA para gerenciamento de vagas, análise de perfis e candidaturas automáticas via e-mail.",
    version="1.0.0"
)

# API Rate Limit middleware
app.add_middleware(APIRateLimitMiddleware, requests_per_minute=settings.API_RATE_LIMIT_PER_MINUTE)

# CORS middleware for custom client API requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up templates directory
templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)

@app.on_event("startup")
async def startup_event():
    # Make sure uploads directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info("Initializing database schema...")
    await init_db()
    start_scheduler()

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down scheduler...")
    shutdown_scheduler()

# Mount routers
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(resumes_router, prefix="/api/resumes", tags=["Resumes"])
app.include_router(applications_router, prefix="/api/applications", tags=["Applications"])
app.include_router(gmail_router, prefix="/api/gmail", tags=["Gmail OAuth"])

@app.get("/", response_class=HTMLResponse)
async def read_dashboard(request: Request):
    """HTML frontend dashboard root loader."""
    # Render dashboard template pass request context
    return templates.TemplateResponse(request, "approve.html", {})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True
    )
