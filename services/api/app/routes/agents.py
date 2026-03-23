"""Agent management routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Agent, AgentTemplate, AgentType
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


# =============================================================================
# Agent Type Pydantic Models
# =============================================================================

class AgentTypeCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    icon: str = "bi-robot"
    color: str = "primary"


class AgentTypeUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class AgentTypeResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    system_prompt: Optional[str]
    icon: str
    color: str
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Agent Pydantic Models
# =============================================================================
class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agent_type: str
    system_prompt: str
    llm_provider: str = "gemini"
    model: str = "gemini-2.0-flash"
    temperature: float = 0.1
    max_tokens: int = 4096
    max_iterations: int = 10
    autonomy_level: str = "recommend"
    allowed_tools: List[str] = []
    allowed_device_patterns: List[str] = ["*"]
    mcp_server_ids: List[int] = []
    knowledge_base_ids: List[int] = []
    api_resource_ids: List[int] = []
    allowed_handoff_agent_ids: Optional[List[int]] = None
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_iterations: Optional[int] = None
    autonomy_level: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    allowed_device_patterns: Optional[List[str]] = None
    mcp_server_ids: Optional[List[int]] = None
    knowledge_base_ids: Optional[List[int]] = None
    api_resource_ids: Optional[List[int]] = None
    allowed_handoff_agent_ids: Optional[List[int]] = None
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
    api_resource_ids: List[int]
    allowed_handoff_agent_ids: Optional[List[int]]
    enabled: bool
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HandoffTargetResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    agent_type: str

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


# =============================================================================
# Agent Type Routes - MUST come before /{agent_id} routes
# =============================================================================

@router.get("/types", response_model=dict)
async def list_agent_types(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List all agent types."""
    types = db.query(AgentType).order_by(AgentType.name).all()
    return {
        "items": [AgentTypeResponse.model_validate(t) for t in types],
        "total": len(types),
    }


@router.post("/types", response_model=AgentTypeResponse)
async def create_agent_type(
    data: AgentTypeCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new agent type."""
    # Check for duplicate name
    existing = db.query(AgentType).filter(AgentType.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Agent type '{data.name}' already exists")

    agent_type = AgentType(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        system_prompt=data.system_prompt,
        icon=data.icon,
        color=data.color,
        is_system=False,
    )

    db.add(agent_type)
    db.commit()
    db.refresh(agent_type)

    return AgentTypeResponse.model_validate(agent_type)


@router.put("/types/{type_id}", response_model=AgentTypeResponse)
async def update_agent_type(
    type_id: int,
    data: AgentTypeUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update an agent type."""
    agent_type = db.query(AgentType).filter(AgentType.id == type_id).first()
    if not agent_type:
        raise HTTPException(status_code=404, detail="Agent type not found")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(agent_type, key, value)

    agent_type.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent_type)

    return AgentTypeResponse.model_validate(agent_type)


@router.delete("/types/{type_id}")
async def delete_agent_type(
    type_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete an agent type (only non-system types can be deleted)."""
    agent_type = db.query(AgentType).filter(AgentType.id == type_id).first()
    if not agent_type:
        raise HTTPException(status_code=404, detail="Agent type not found")

    if agent_type.is_system:
        raise HTTPException(status_code=400, detail="System agent types cannot be deleted")

    # Check if any agents use this type
    agents_using = db.query(Agent).filter(Agent.agent_type == agent_type.name).count()
    if agents_using > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete type '{agent_type.name}' - {agents_using} agent(s) are using it"
        )

    db.delete(agent_type)
    db.commit()

    return {"message": "Agent type deleted"}


# =============================================================================
# Agent Routes
# =============================================================================

@router.get("", response_model=dict)
async def list_agents(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    agent_type: Optional[str] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all manually created agents (excludes ephemeral job agents)."""
    query = db.query(Agent).filter(
        Agent.is_template == False,
        Agent.is_ephemeral == False,  # Exclude ephemeral agents
    )

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


@router.get("/handoff-targets", response_model=dict)
async def get_handoff_targets(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    exclude_agent_id: Optional[int] = Query(default=None),
):
    """Get agents available for handoff.

    Returns enabled agents that can be handed off to.
    Optionally excludes a specific agent (e.g., the current agent).
    """
    query = db.query(Agent).filter(
        Agent.is_template == False,
        Agent.enabled == True,
    )

    if exclude_agent_id:
        query = query.filter(Agent.id != exclude_agent_id)

    agents = query.order_by(Agent.name).all()

    return {
        "items": [HandoffTargetResponse.model_validate(a) for a in agents],
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
    """Create a new agent.

    Note: Empty mcp_server_ids and knowledge_base_ids mean "use ALL available".
    This ensures agents automatically get access to new resources when added.
    """
    # Use explicitly provided tools, knowledge bases, MCP servers, and API resources
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
        allowed_tools=data.allowed_tools,  # Use provided tools
        allowed_device_patterns=data.allowed_device_patterns,
        mcp_server_ids=data.mcp_server_ids,  # Use provided MCP servers
        knowledge_base_ids=data.knowledge_base_ids,  # Use provided knowledge bases
        api_resource_ids=data.api_resource_ids,  # Use provided API resources
        allowed_handoff_agent_ids=data.allowed_handoff_agent_ids,
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
    """Update an agent.

    Note: Empty mcp_server_ids and knowledge_base_ids mean "use ALL available".
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)

    # No longer override tools/devices - use what's provided
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
    from netagent_core.db import JobTask

    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_name = agent.name

    # Clear references from job_tasks before deleting
    db.query(JobTask).filter(JobTask.agent_id == agent_id).update(
        {"agent_id": None}, synchronize_session=False
    )
    db.query(JobTask).filter(JobTask.ephemeral_agent_id == agent_id).update(
        {"ephemeral_agent_id": None}, synchronize_session=False
    )

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
    """Duplicate an agent.

    Note: Empty mcp_server_ids and knowledge_base_ids mean "use ALL available".
    """
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
        allowed_tools=agent.allowed_tools,  # Copy original tools
        allowed_device_patterns=agent.allowed_device_patterns,
        mcp_server_ids=agent.mcp_server_ids,  # Copy original MCP servers
        knowledge_base_ids=agent.knowledge_base_ids,  # Copy original knowledge bases
        api_resource_ids=agent.api_resource_ids,  # Copy original API resources
        allowed_handoff_agent_ids=agent.allowed_handoff_agent_ids,
        enabled=False,
        created_by=user.id,
    )

    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)

    return AgentResponse.model_validate(new_agent)


@router.get("/{agent_id}/handoff-targets", response_model=dict)
async def get_agent_handoff_targets(
    agent_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get allowed handoff targets for a specific agent.

    If the agent has allowed_handoff_agent_ids set, returns only those agents.
    Otherwise, returns all enabled agents except itself.
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    query = db.query(Agent).filter(
        Agent.is_template == False,
        Agent.enabled == True,
        Agent.id != agent_id,  # Exclude self
    )

    # If allowed_handoff_agent_ids is set, filter to only those
    if agent.allowed_handoff_agent_ids:
        query = query.filter(Agent.id.in_(agent.allowed_handoff_agent_ids))

    agents = query.order_by(Agent.name).all()

    return {
        "items": [HandoffTargetResponse.model_validate(a) for a in agents],
        "restricted": agent.allowed_handoff_agent_ids is not None,
    }
