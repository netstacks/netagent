"""Audit log routes."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, AuditLog, AgentSession, AgentAction
from netagent_core.auth import get_current_user, ALBUser

router = APIRouter()


class AuditLogResponse(BaseModel):
    id: int
    event_type: str
    event_category: Optional[str]
    user_id: Optional[int]
    user_email: Optional[str]
    resource_type: Optional[str]
    resource_id: Optional[int]
    resource_name: Optional[str]
    action: Optional[str]
    details: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=dict)
async def query_audit_log(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    event_type: Optional[str] = None,
    event_category: Optional[str] = None,
    user_email: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
):
    """Query audit log with filters."""
    query = db.query(AuditLog)

    if event_type:
        query = query.filter(AuditLog.event_type == event_type)
    if event_category:
        query = query.filter(AuditLog.event_category == event_category)
    if user_email:
        query = query.filter(AuditLog.user_email.ilike(f"%{user_email}%"))
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if resource_id:
        query = query.filter(AuditLog.resource_id == resource_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if start_date:
        query = query.filter(AuditLog.created_at >= start_date)
    if end_date:
        query = query.filter(AuditLog.created_at <= end_date)

    total = query.count()
    logs = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [AuditLogResponse.model_validate(log) for log in logs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/categories")
async def list_categories(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List available event categories."""
    categories = db.query(AuditLog.event_category).distinct().all()
    return {
        "categories": [c[0] for c in categories if c[0]],
    }


@router.get("/event-types")
async def list_event_types(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List available event types."""
    event_types = db.query(AuditLog.event_type).distinct().all()
    return {
        "event_types": [e[0] for e in event_types if e[0]],
    }


@router.get("/sessions/{session_id}/timeline")
async def get_session_timeline(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get detailed timeline for an agent session."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get all actions for this session
    actions = db.query(AgentAction).filter(
        AgentAction.session_id == session_id
    ).order_by(AgentAction.created_at).all()

    timeline = []
    for action in actions:
        item = {
            "id": action.id,
            "timestamp": action.created_at.isoformat(),
            "type": action.action_type,
            "status": action.status,
        }

        if action.action_type == "thought":
            item["reasoning"] = action.reasoning
        elif action.action_type == "tool_call":
            item["tool_name"] = action.tool_name
            item["tool_input"] = action.tool_input
            item["duration_ms"] = action.duration_ms
        elif action.action_type == "tool_result":
            item["tool_name"] = action.tool_name
            item["tool_output"] = action.tool_output
        elif action.action_type == "approval_request":
            item["risk_level"] = action.risk_level
            item["requires_approval"] = action.requires_approval

        if action.error_message:
            item["error"] = action.error_message

        timeline.append(item)

    return {
        "session_id": session_id,
        "status": session.status,
        "created_at": session.created_at.isoformat(),
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "message_count": session.message_count,
        "tool_call_count": session.tool_call_count,
        "token_count": session.token_count,
        "timeline": timeline,
    }


@router.get("/workflows/{run_id}/timeline")
async def get_workflow_timeline(
    run_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get execution timeline for a workflow run."""
    from netagent_core.db import WorkflowRun, WorkflowNodeExecution

    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    nodes = db.query(WorkflowNodeExecution).filter(
        WorkflowNodeExecution.workflow_run_id == run_id
    ).order_by(WorkflowNodeExecution.started_at).all()

    timeline = []
    for node in nodes:
        item = {
            "id": node.id,
            "node_id": node.node_id,
            "node_type": node.node_type,
            "status": node.status,
            "started_at": node.started_at.isoformat() if node.started_at else None,
            "completed_at": node.completed_at.isoformat() if node.completed_at else None,
        }

        if node.input_data:
            item["input_summary"] = _summarize_data(node.input_data)
        if node.output_data:
            item["output_summary"] = _summarize_data(node.output_data)
        if node.error_message:
            item["error"] = node.error_message

        timeline.append(item)

    return {
        "run_id": run_id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "timeline": timeline,
    }


def _summarize_data(data: dict, max_length: int = 100) -> str:
    """Create a brief summary of data for timeline display."""
    import json
    text = json.dumps(data)
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text
