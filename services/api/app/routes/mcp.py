"""MCP server management routes."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, MCPServer
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType, encrypt_value, decrypt_value
from netagent_core.mcp import MCPClient, MCPError

router = APIRouter()


class MCPServerCreate(BaseModel):
    name: str
    description: Optional[str] = None
    base_url: str
    transport: str = "http"
    auth_type: Optional[str] = None
    auth_token: Optional[str] = None
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    auth_token: Optional[str] = None
    enabled: Optional[bool] = None


class MCPServerResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    base_url: str
    transport: str
    auth_type: Optional[str]
    tools: List[dict]
    last_discovered_at: Optional[datetime]
    health_status: str
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/servers", response_model=dict)
async def list_mcp_servers(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all MCP servers."""
    query = db.query(MCPServer)

    if enabled is not None:
        query = query.filter(MCPServer.enabled == enabled)

    total = query.count()
    servers = query.order_by(MCPServer.name).offset(offset).limit(limit).all()

    return {
        "items": [MCPServerResponse.model_validate(s) for s in servers],
        "total": total,
    }


@router.get("/servers/{server_id}", response_model=MCPServerResponse)
async def get_mcp_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get MCP server by ID."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    return MCPServerResponse.model_validate(server)


@router.post("/servers")
async def create_mcp_server(
    data: MCPServerCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Add a new MCP server."""
    auth_config_encrypted = None
    if data.auth_token:
        auth_config_encrypted = encrypt_value(data.auth_token)

    server = MCPServer(
        name=data.name,
        description=data.description,
        base_url=data.base_url.rstrip("/"),
        transport=data.transport,
        auth_type=data.auth_type,
        auth_config_encrypted=auth_config_encrypted,
        enabled=data.enabled,
    )

    db.add(server)
    db.commit()
    db.refresh(server)

    audit_log(
        db,
        AuditEventType.MCP_SERVER_CREATED,
        user=user,
        resource_type="mcp_server",
        resource_id=server.id,
        resource_name=server.name,
        action="create",
    )

    return {"id": server.id, "message": "MCP server added"}


@router.put("/servers/{server_id}")
async def update_mcp_server(
    server_id: int,
    data: MCPServerUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update MCP server configuration."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if data.name is not None:
        server.name = data.name
    if data.description is not None:
        server.description = data.description
    if data.base_url is not None:
        server.base_url = data.base_url.rstrip("/")
    if data.auth_type is not None:
        server.auth_type = data.auth_type
    if data.auth_token is not None:
        server.auth_config_encrypted = encrypt_value(data.auth_token)
    if data.enabled is not None:
        server.enabled = data.enabled

    db.commit()

    audit_log(
        db,
        AuditEventType.MCP_SERVER_UPDATED,
        user=user,
        resource_type="mcp_server",
        resource_id=server.id,
        resource_name=server.name,
        action="update",
    )

    return {"message": "MCP server updated"}


@router.delete("/servers/{server_id}")
async def delete_mcp_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Remove MCP server."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    server_name = server.name
    db.delete(server)
    db.commit()

    audit_log(
        db,
        AuditEventType.MCP_SERVER_DELETED,
        user=user,
        resource_type="mcp_server",
        resource_id=server_id,
        resource_name=server_name,
        action="delete",
    )

    return {"message": "MCP server removed"}


@router.post("/servers/{server_id}/discover")
async def discover_tools(
    server_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Discover available tools from MCP server."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    # Get auth token if configured
    auth_token = None
    if server.auth_config_encrypted:
        auth_token = decrypt_value(server.auth_config_encrypted)

    try:
        client = MCPClient(
            base_url=server.base_url,
            auth_type=server.auth_type,
            auth_token=auth_token,
        )

        tools = await client.list_tools()

        # Update server with discovered tools
        server.tools = tools
        server.last_discovered_at = datetime.utcnow()
        server.health_status = "healthy"
        db.commit()

        return {
            "tools": tools,
            "count": len(tools),
        }

    except MCPError as e:
        server.health_status = "unhealthy"
        db.commit()
        raise HTTPException(status_code=502, detail=f"MCP error: {e.message}")
    except Exception as e:
        server.health_status = "unhealthy"
        db.commit()
        raise HTTPException(status_code=502, detail=f"Connection failed: {str(e)}")


@router.get("/servers/{server_id}/tools")
async def list_server_tools(
    server_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List cached tools for MCP server."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    return {
        "tools": server.tools or [],
        "last_discovered_at": server.last_discovered_at.isoformat() if server.last_discovered_at else None,
    }


class MCPTestRequest(BaseModel):
    """Request for testing MCP server connectivity."""
    base_url: str
    auth_type: Optional[str] = None
    auth_token: Optional[str] = None


@router.post("/test-discover")
async def test_discover_tools(
    data: MCPTestRequest,
    user: ALBUser = Depends(get_current_user),
):
    """Test MCP server discovery without creating a server record.

    Useful for validating connectivity before adding a server.
    """
    try:
        client = MCPClient(
            base_url=data.base_url,
            auth_type=data.auth_type,
            auth_token=data.auth_token,
        )

        tools = await client.list_tools()

        return {
            "status": "success",
            "tools": tools,
            "count": len(tools),
        }

    except MCPError as e:
        raise HTTPException(status_code=502, detail=f"MCP error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Connection failed: {str(e)}")


class MCPToolCallRequest(BaseModel):
    """Request for calling an MCP tool."""
    tool_name: str
    arguments: dict = {}


@router.post("/servers/{server_id}/call")
async def call_server_tool(
    server_id: int,
    data: MCPToolCallRequest,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Call a tool on an MCP server directly (for testing).

    This endpoint allows testing MCP tool execution before using in agents.
    """
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    auth_token = None
    if server.auth_config_encrypted:
        auth_token = decrypt_value(server.auth_config_encrypted)

    try:
        client = MCPClient(
            base_url=server.base_url,
            auth_type=server.auth_type,
            auth_token=auth_token,
        )

        result = await client.call_tool(data.tool_name, data.arguments)

        return {
            "status": "success",
            "result": result,
        }

    except MCPError as e:
        raise HTTPException(status_code=502, detail=f"MCP error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tool call failed: {str(e)}")


@router.post("/servers/{server_id}/health")
async def check_health(
    server_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Check MCP server health."""
    server = db.query(MCPServer).filter(MCPServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    auth_token = None
    if server.auth_config_encrypted:
        auth_token = decrypt_value(server.auth_config_encrypted)

    try:
        client = MCPClient(
            base_url=server.base_url,
            auth_type=server.auth_type,
            auth_token=auth_token,
        )

        healthy = await client.health_check()
        server.health_status = "healthy" if healthy else "unhealthy"
        db.commit()

        return {"healthy": healthy, "status": server.health_status}

    except Exception as e:
        server.health_status = "unhealthy"
        db.commit()
        return {"healthy": False, "status": "unhealthy", "error": str(e)}
