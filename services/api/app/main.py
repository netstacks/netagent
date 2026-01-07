"""API service - main application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add shared package to path
import sys
sys.path.insert(0, '/app/shared')

from netagent_core.db import init_db

from routes import (
    agents,
    chat,
    workflows,
    knowledge,
    devices,
    mcp,
    approvals,
    audit,
    triggers,
    users,
    stats,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting NetAgent API Service")

    # Initialize database
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized")

    # Seed default data
    from services.seed import seed_agent_templates
    seed_agent_templates()

    yield

    logger.info("Shutting down NetAgent API Service")


app = FastAPI(
    title="NetAgent API",
    description="AI Agent Platform for Network Engineering",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(agents.router, prefix="/api/agents", tags=["Agents"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["Workflows"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["Knowledge"])
app.include_router(devices.router, prefix="/api/devices", tags=["Devices"])
app.include_router(mcp.router, prefix="/api/mcp", tags=["MCP"])
app.include_router(approvals.router, prefix="/api/approvals", tags=["Approvals"])
app.include_router(audit.router, prefix="/api/audit", tags=["Audit"])
app.include_router(triggers.router, prefix="/api/triggers", tags=["Triggers"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(stats.router, prefix="/api/stats", tags=["Stats"])


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "api"}


@app.get("/health")
async def health_simple():
    """Simple health check."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
