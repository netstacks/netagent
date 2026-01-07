"""Frontend service - serves HTML templates."""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

# Add shared package to path
import sys
sys.path.insert(0, '/app/shared')

from netagent_core.auth import get_current_user, get_current_user_optional, ALBUser
from netagent_core.db import init_db

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

# Templates
templates = Jinja2Templates(directory="/app/templates")


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


@app.get("/workflows", response_class=HTMLResponse)
async def workflows_list(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Workflows list page."""
    return templates.TemplateResponse(
        "workflows.html",
        {
            "request": request,
            "user": user,
            "active_page": "workflows",
            "pending_approvals": 0,
        }
    )


@app.get("/workflows/new", response_class=HTMLResponse)
async def workflow_builder(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Workflow builder page."""
    return templates.TemplateResponse(
        "workflow_builder.html",
        {
            "request": request,
            "user": user,
            "active_page": "workflow_builder",
            "pending_approvals": 0,
        }
    )


@app.get("/workflows/{workflow_id}", response_class=HTMLResponse)
async def workflow_detail(
    request: Request,
    workflow_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Workflow detail/edit page."""
    return templates.TemplateResponse(
        "workflow_builder.html",
        {
            "request": request,
            "user": user,
            "active_page": "workflows",
            "workflow_id": workflow_id,
            "pending_approvals": 0,
        }
    )


@app.get("/workflows/{workflow_id}/runs/{run_id}", response_class=HTMLResponse)
async def workflow_run(
    request: Request,
    workflow_id: int,
    run_id: int,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Workflow run detail page."""
    return templates.TemplateResponse(
        "workflow_run.html",
        {
            "request": request,
            "user": user,
            "active_page": "workflows",
            "workflow_id": workflow_id,
            "run_id": run_id,
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


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "frontend"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
