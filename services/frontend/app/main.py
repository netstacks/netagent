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
