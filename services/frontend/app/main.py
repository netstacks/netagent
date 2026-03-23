"""Frontend service - serves HTML templates."""

import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

# Add shared package to path
import sys
sys.path.insert(0, '/app/shared')

from netagent_core.auth import get_current_user, get_current_user_optional, ALBUser
from netagent_core.db import init_db

# API service URL for fetching data
API_URL = os.getenv("API_URL", "http://api:8000")


async def fetch_from_api(path: str, request: Request):
    """Fetch data from the API service, forwarding auth headers."""
    headers = {}
    # Forward ALB authentication headers
    if "x-amzn-oidc-data" in request.headers:
        headers["x-amzn-oidc-data"] = request.headers["x-amzn-oidc-data"]
    if "x-amzn-oidc-identity" in request.headers:
        headers["x-amzn-oidc-identity"] = request.headers["x-amzn-oidc-identity"]
    if "x-amzn-oidc-accesstoken" in request.headers:
        headers["x-amzn-oidc-accesstoken"] = request.headers["x-amzn-oidc-accesstoken"]

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_URL}{path}", headers=headers, timeout=30.0)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting NetAgent Frontend Service")
    yield
    logger.info("Shutting down NetAgent Frontend Service")


app = FastAPI(
    title="NetAgent Frontend",
    description="NetAgent UI Service",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# Templates - auto_reload for development
templates = Jinja2Templates(directory="/app/templates")
templates.env.auto_reload = True


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Dashboard page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "active_page": "dashboard",
            "pending_approvals": 0,
        }
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Agents list page."""
    return templates.TemplateResponse(
        "agents.html",
        {
            "request": request,
            "user": user,
            "active_page": "agents",
            "pending_approvals": 0,
        }
    )


@app.get("/agents/new", response_class=HTMLResponse)
async def agent_create(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Create agent page."""
    return templates.TemplateResponse(
        "agent_create.html",
        {
            "request": request,
            "user": user,
            "active_page": "agent_create",
            "agent_id": None,
            "pending_approvals": 0,
        }
    )


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    agent_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Agent detail/edit page."""
    return templates.TemplateResponse(
        "agent_create.html",
        {
            "request": request,
            "user": user,
            "active_page": "agents",
            "agent_id": agent_id,
            "pending_approvals": 0,
        }
    )


@app.get("/agents/{agent_id}/chat", response_class=HTMLResponse)
async def agent_chat(
    request: Request,
    agent_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Agent chat interface."""
    return templates.TemplateResponse(
        "agent_chat.html",
        {
            "request": request,
            "user": user,
            "active_page": "agents",
            "agent_id": agent_id,
            "pending_approvals": 0,
        }
    )


@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Knowledge bases page."""
    return templates.TemplateResponse(
        "knowledge.html",
        {
            "request": request,
            "user": user,
            "active_page": "knowledge",
            "pending_approvals": 0,
        }
    )


@app.get("/knowledge/{kb_id}", response_class=HTMLResponse)
async def knowledge_detail(
    kb_id: int,
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Knowledge base detail page - view documents."""
    return templates.TemplateResponse(
        "knowledge_detail.html",
        {
            "request": request,
            "user": user,
            "active_page": "knowledge",
            "pending_approvals": 0,
            "kb_id": kb_id,
        }
    )


@app.get("/devices", response_class=HTMLResponse)
async def devices_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Device credentials page."""
    return templates.TemplateResponse(
        "devices.html",
        {
            "request": request,
            "user": user,
            "active_page": "devices",
            "pending_approvals": 0,
        }
    )


@app.get("/mcp", response_class=HTMLResponse)
async def mcp_servers(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """MCP servers page."""
    return templates.TemplateResponse(
        "mcp_servers.html",
        {
            "request": request,
            "user": user,
            "active_page": "mcp",
            "pending_approvals": 0,
        }
    )


@app.get("/api-resources", response_class=HTMLResponse)
async def api_resources(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """API Resources page."""
    return templates.TemplateResponse(
        "api_resources.html",
        {
            "request": request,
            "user": user,
            "active_page": "api_resources",
            "pending_approvals": 0,
        }
    )


@app.get("/settings/api-docs", response_class=HTMLResponse)
async def api_docs(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """API Documentation page."""
    return templates.TemplateResponse(
        "api_docs.html",
        {
            "request": request,
            "user": user,
            "active_page": "api_docs",
            "pending_approvals": 0,
        }
    )


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Approvals page."""
    return templates.TemplateResponse(
        "approvals.html",
        {
            "request": request,
            "user": user,
            "active_page": "approvals",
            "pending_approvals": 0,
        }
    )


@app.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Audit log page."""
    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "user": user,
            "active_page": "audit",
            "pending_approvals": 0,
        }
    )


@app.get("/alerts", response_class=HTMLResponse)
async def alerts_dashboard(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Alerts dashboard page."""
    return templates.TemplateResponse(
        "alerts.html",
        {
            "request": request,
            "user": user,
            "active_page": "alerts",
            "pending_approvals": 0,
        }
    )


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Sessions list page."""
    return templates.TemplateResponse(
        "sessions.html",
        {
            "request": request,
            "user": user,
            "active_page": "sessions",
            "pending_approvals": 0,
        }
    )


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail(
    request: Request,
    session_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Session detail page - redirects to agent chat with session loaded."""
    # Fetch the session to get the agent_id
    try:
        session_data = await fetch_from_api(f"/api/chat/sessions/{session_id}", request)
        if session_data:
            agent_id = session_data.get("agent_id")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"/agents/{agent_id}/chat?session={session_id}")
    except Exception:
        pass

    # If session not found, show sessions list
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/sessions")


@app.get("/settings", response_class=HTMLResponse)
async def settings(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Settings page."""
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "active_page": "settings",
            "pending_approvals": 0,
        }
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Jobs list page."""
    return templates.TemplateResponse(
        "jobs.html",
        {
            "request": request,
            "user": user,
            "active_page": "jobs",
            "pending_approvals": 0,
        }
    )


@app.get("/jobs/new", response_class=HTMLResponse)
async def job_create(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Create job page."""
    return templates.TemplateResponse(
        "job_create.html",
        {
            "request": request,
            "user": user,
            "active_page": "jobs",
            "pending_approvals": 0,
            "job_id": None,  # Create mode
        }
    )


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
async def job_edit(
    request: Request,
    job_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Edit job page - works for any job status.

    For pending jobs: edits in place
    For completed/failed/cancelled jobs: creates a new job as copy
    """
    # Fetch job data from API
    job_data = await fetch_from_api(f"/api/jobs/{job_id}", request)
    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")

    # Pass the job status so the UI knows whether to edit or clone
    return templates.TemplateResponse(
        "job_create.html",
        {
            "request": request,
            "user": user,
            "active_page": "jobs",
            "pending_approvals": 0,
            "job_id": job_id,  # Pass job_id for edit mode
            "job_status": job_data.get("status"),  # Let UI know the status
        }
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    job_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Job detail page."""
    # Fetch job data from API
    job_data = await fetch_from_api(f"/api/jobs/{job_id}", request)
    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")

    # Determine status color
    status_colors = {
        "pending": "secondary",
        "awaiting_confirmation": "info",
        "queued": "info",
        "executing": "primary",
        "validating": "warning",
        "awaiting_approval": "warning",
        "delivering": "info",
        "completed": "success",
        "failed": "danger",
        "cancelled": "dark",
    }
    status_color = status_colors.get(job_data.get("status", ""), "secondary")

    # Create a simple job object for the template
    class JobObj:
        def __init__(self, data):
            for key, value in data.items():
                if key == "created_at" and value:
                    from datetime import datetime
                    try:
                        setattr(self, key, datetime.fromisoformat(value.replace("Z", "+00:00")))
                    except:
                        setattr(self, key, None)
                else:
                    setattr(self, key, value)

    job = JobObj(job_data)

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "user": user,
            "active_page": "jobs",
            "pending_approvals": 0,
            "job": job,
            "status_color": status_color,
            "task_status_color": lambda s: status_colors.get(s, "secondary"),
            "task_duration": _calculate_task_duration,
        }
    )


def _calculate_task_duration(task):
    """Calculate task duration as a human-readable string."""
    if not hasattr(task, "started_at") or not task.started_at:
        return "-"

    from datetime import datetime

    try:
        if isinstance(task.started_at, str):
            start = datetime.fromisoformat(task.started_at.replace("Z", "+00:00"))
        else:
            start = task.started_at

        if hasattr(task, "completed_at") and task.completed_at:
            if isinstance(task.completed_at, str):
                end = datetime.fromisoformat(task.completed_at.replace("Z", "+00:00"))
            else:
                end = task.completed_at
        else:
            end = datetime.utcnow()

        seconds = int((end - start).total_seconds())

        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    except:
        return "-"


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Scheduled tasks page."""
    return templates.TemplateResponse(
        "scheduled_tasks.html",
        {
            "request": request,
            "user": user,
            "active_page": "scheduled_tasks",
            "pending_approvals": 0,
        }
    )


@app.get("/live-sessions", response_class=HTMLResponse)
async def live_sessions(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Live sessions monitoring page."""
    return templates.TemplateResponse(
        "live_sessions.html",
        {
            "request": request,
            "user": user,
            "active_page": "live_sessions",
            "pending_approvals": 0,
        }
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "frontend"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
