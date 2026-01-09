"""Scheduled tasks management routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, ScheduledTask, Agent, AgentSession
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


# Pydantic models
class ScheduledTaskCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agent_id: int
    schedule_cron: str
    prompt: str
    enabled: bool = True


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_id: Optional[int] = None
    schedule_cron: Optional[str] = None
    prompt: Optional[str] = None
    enabled: Optional[bool] = None


class AgentSummary(BaseModel):
    id: int
    name: str
    agent_type: str

    class Config:
        from_attributes = True


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    agent_id: int
    agent: Optional[AgentSummary]
    schedule_cron: str
    prompt: str
    enabled: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    last_session_id: Optional[int]
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=dict)
async def list_scheduled_tasks(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    agent_id: Optional[int] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all scheduled tasks."""
    query = db.query(ScheduledTask)

    if enabled is not None:
        query = query.filter(ScheduledTask.enabled == enabled)
    if agent_id is not None:
        query = query.filter(ScheduledTask.agent_id == agent_id)

    total = query.count()
    tasks = query.order_by(ScheduledTask.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [ScheduledTaskResponse.model_validate(t) for t in tasks],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get scheduled task by ID."""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    return ScheduledTaskResponse.model_validate(task)


@router.post("", response_model=ScheduledTaskResponse)
async def create_scheduled_task(
    data: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new scheduled task."""
    # Verify agent exists
    agent = db.query(Agent).filter(Agent.id == data.agent_id).first()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not found")

    task = ScheduledTask(
        name=data.name,
        description=data.description,
        agent_id=data.agent_id,
        schedule_cron=data.schedule_cron,
        prompt=data.prompt,
        enabled=data.enabled,
        created_by=user.id,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    audit_log(
        db,
        AuditEventType.AGENT_CREATED,  # Reuse event type
        user=user,
        resource_type="scheduled_task",
        resource_id=task.id,
        resource_name=task.name,
        action="create",
    )

    return ScheduledTaskResponse.model_validate(task)


@router.put("/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    task_id: int,
    data: ScheduledTaskUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update a scheduled task."""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    # Verify agent exists if changing
    if data.agent_id is not None:
        agent = db.query(Agent).filter(Agent.id == data.agent_id).first()
        if not agent:
            raise HTTPException(status_code=400, detail="Agent not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    audit_log(
        db,
        AuditEventType.AGENT_UPDATED,
        user=user,
        resource_type="scheduled_task",
        resource_id=task.id,
        resource_name=task.name,
        action="update",
        details=update_data,
    )

    return ScheduledTaskResponse.model_validate(task)


@router.delete("/{task_id}")
async def delete_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete a scheduled task."""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    task_name = task.name
    db.delete(task)
    db.commit()

    audit_log(
        db,
        AuditEventType.AGENT_DELETED,
        user=user,
        resource_type="scheduled_task",
        resource_id=task_id,
        resource_name=task_name,
        action="delete",
    )

    return {"message": "Scheduled task deleted"}


@router.post("/{task_id}/trigger", response_model=dict)
async def trigger_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Manually trigger a scheduled task."""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    agent = db.query(Agent).filter(Agent.id == task.agent_id).first()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not found")

    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")

    # Create agent session
    session = AgentSession(
        agent_id=task.agent_id,
        status="pending",
        trigger_type="manual_scheduled",
        user_id=user.id,
        context={
            "scheduled_task_id": task.id,
            "scheduled_task_name": task.name,
            "triggered_by": user.email,
            "manual_trigger": True,
        },
    )
    db.add(session)

    # Update task status
    task.last_run_at = datetime.utcnow()
    task.last_run_status = "running"
    db.commit()
    db.refresh(session)

    task.last_session_id = session.id
    db.commit()

    # Queue execution
    try:
        from celery import current_app
        current_app.send_task(
            'tasks.agent_executor.execute_agent_session',
            args=[session.id, task.prompt],
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to queue agent execution: {e}")

    audit_log(
        db,
        AuditEventType.AGENT_CHAT_STARTED,
        user=user,
        resource_type="scheduled_task",
        resource_id=task.id,
        resource_name=task.name,
        action="manual_trigger",
    )

    return {
        "session_id": session.id,
        "task_id": task.id,
        "task_name": task.name,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "status": "pending",
        "message": "Scheduled task triggered",
    }


@router.post("/{task_id}/toggle", response_model=ScheduledTaskResponse)
async def toggle_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Toggle a scheduled task's enabled status."""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    task.enabled = not task.enabled
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    audit_log(
        db,
        AuditEventType.AGENT_UPDATED,
        user=user,
        resource_type="scheduled_task",
        resource_id=task.id,
        resource_name=task.name,
        action="toggle",
        details={"enabled": task.enabled},
    )

    return ScheduledTaskResponse.model_validate(task)
