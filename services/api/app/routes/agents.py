"""Agent management routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Agent, AgentTemplate
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


# Pydantic models
class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agent_type: str
    system_prompt: str
    model: str = "gemini-2.0-flash"
    temperature: float = 0.1
    max_tokens: int = 4096
    max_iterations: int = 10
    autonomy_level: str = "recommend"
    allowed_tools: List[str] = []
    allowed_device_patterns: List[str] = ["*"]
    mcp_server_ids: List[int] = []
    knowledge_base_ids: List[int] = []
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_iterations: Optional[int] = None
    autonomy_level: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    allowed_device_patterns: Optional[List[str]] = None
    mcp_server_ids: Optional[List[int]] = None
    knowledge_base_ids: Optional[List[int]] = None
    enabled: Optional[bool] = None


class AgentResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    agent_type: str
    system_prompt: str
    model: str
    temperature: float
    max_tokens: int
    max_iterations: int
    autonomy_level: str
    allowed_tools: List[str]
    allowed_device_patterns: List[str]
    mcp_server_ids: List[int]
    knowledge_base_ids: List[int]
    enabled: bool
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentTemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    agent_type: str
    system_prompt: str
    default_tools: List[str]
    default_model: str
    icon: Optional[str]
    is_builtin: bool

    class Config:
        from_attributes = True


@router.get("", response_model=dict)
async def list_agents(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    agent_type: Optional[str] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all agents."""
    query = db.query(Agent).filter(Agent.is_template == False)

    if enabled is not None:
        query = query.filter(Agent.enabled == enabled)
    if agent_type:
        query = query.filter(Agent.agent_type == agent_type)

    total = query.count()
    agents = query.order_by(Agent.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [AgentResponse.model_validate(a) for a in agents],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/templates", response_model=dict)
async def list_agent_templates(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List agent templates."""
    templates = db.query(AgentTemplate).order_by(AgentTemplate.name).all()

    return {
        "items": [AgentTemplateResponse.model_validate(t) for t in templates],
    }


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get agent by ID."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentResponse.model_validate(agent)


@router.post("", response_model=AgentResponse)
async def create_agent(
    data: AgentCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new agent."""
    agent = Agent(
        name=data.name,
        description=data.description,
        agent_type=data.agent_type,
        system_prompt=data.system_prompt,
        model=data.model,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        max_iterations=data.max_iterations,
        autonomy_level=data.autonomy_level,
        allowed_tools=data.allowed_tools,
        allowed_device_patterns=data.allowed_device_patterns,
        mcp_server_ids=data.mcp_server_ids,
        knowledge_base_ids=data.knowledge_base_ids,
        enabled=data.enabled,
        created_by=user.id,
    )

    db.add(agent)
    db.commit()
    db.refresh(agent)

    # Audit log
    audit_log(
        db,
        AuditEventType.AGENT_CREATED,
        user=user,
        resource_type="agent",
        resource_id=agent.id,
        resource_name=agent.name,
        action="create",
    )

    return AgentResponse.model_validate(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int,
    data: AgentUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update an agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(agent, key, value)

    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)

    # Audit log
    audit_log(
        db,
        AuditEventType.AGENT_UPDATED,
        user=user,
        resource_type="agent",
        resource_id=agent.id,
        resource_name=agent.name,
        action="update",
        details=update_data,
    )

    return AgentResponse.model_validate(agent)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete an agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_name = agent.name
    db.delete(agent)
    db.commit()

    # Audit log
    audit_log(
        db,
        AuditEventType.AGENT_DELETED,
        user=user,
        resource_type="agent",
        resource_id=agent_id,
        resource_name=agent_name,
        action="delete",
    )

    return {"message": "Agent deleted"}


@router.post("/{agent_id}/duplicate", response_model=AgentResponse)
async def duplicate_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Duplicate an agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_agent = Agent(
        name=f"{agent.name} (Copy)",
        description=agent.description,
        agent_type=agent.agent_type,
        system_prompt=agent.system_prompt,
        model=agent.model,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        max_iterations=agent.max_iterations,
        autonomy_level=agent.autonomy_level,
        allowed_tools=agent.allowed_tools,
        allowed_device_patterns=agent.allowed_device_patterns,
        mcp_server_ids=agent.mcp_server_ids,
        knowledge_base_ids=agent.knowledge_base_ids,
        enabled=False,
        created_by=user.id,
    )

    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)

    return AgentResponse.model_validate(new_agent)
