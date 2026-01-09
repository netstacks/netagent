"""Dashboard statistics routes."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from netagent_core.db import get_db, Agent, Workflow, Approval, AgentSession, KnowledgeBase
from netagent_core.auth import get_current_user, ALBUser

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get dashboard statistics."""
    # Count active agents
    agents_count = db.query(Agent).filter(
        Agent.enabled == True,
        Agent.is_template == False,
    ).count()

    # Count workflows
    workflows_count = db.query(Workflow).filter(
        Workflow.enabled == True
    ).count()

    # Count pending approvals
    pending_approvals = db.query(Approval).filter(
        Approval.status == "pending"
    ).count()

    # Count sessions today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sessions_today = db.query(AgentSession).filter(
        AgentSession.created_at >= today_start
    ).count()

    # Count knowledge bases
    knowledge_bases_count = db.query(KnowledgeBase).count()

    # Count total tool calls today
    tool_calls_today = db.query(func.sum(AgentSession.tool_call_count)).filter(
        AgentSession.created_at >= today_start
    ).scalar() or 0

    return {
        "agents": agents_count,
        "workflows": workflows_count,
        "pending_approvals": pending_approvals,
        "sessions_today": sessions_today,
        "knowledge_bases": knowledge_bases_count,
        "tool_calls_today": tool_calls_today,
    }


@router.get("/agents/{agent_id}")
async def get_agent_stats(
    agent_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get statistics for a specific agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return {"error": "Agent not found"}

    # Total sessions
    total_sessions = db.query(AgentSession).filter(
        AgentSession.agent_id == agent_id
    ).count()

    # Sessions in last 7 days
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_sessions = db.query(AgentSession).filter(
        AgentSession.agent_id == agent_id,
        AgentSession.created_at >= week_ago
    ).count()

    # Total messages and tokens
    session_stats = db.query(
        func.sum(AgentSession.message_count).label("total_messages"),
        func.sum(AgentSession.token_count).label("total_tokens"),
        func.sum(AgentSession.tool_call_count).label("total_tool_calls"),
    ).filter(AgentSession.agent_id == agent_id).first()

    return {
        "agent_id": agent_id,
        "total_sessions": total_sessions,
        "recent_sessions": recent_sessions,
        "total_messages": session_stats.total_messages or 0,
        "total_tokens": session_stats.total_tokens or 0,
        "total_tool_calls": session_stats.total_tool_calls or 0,
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow_stats(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get statistics for a specific workflow."""
    from netagent_core.db import WorkflowRun

    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        return {"error": "Workflow not found"}

    # Total runs
    total_runs = db.query(WorkflowRun).filter(
        WorkflowRun.workflow_id == workflow_id
    ).count()

    # Runs by status
    status_counts = db.query(
        WorkflowRun.status,
        func.count(WorkflowRun.id).label("count")
    ).filter(
        WorkflowRun.workflow_id == workflow_id
    ).group_by(WorkflowRun.status).all()

    # Recent runs (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_runs = db.query(WorkflowRun).filter(
        WorkflowRun.workflow_id == workflow_id,
        WorkflowRun.created_at >= week_ago
    ).count()

    return {
        "workflow_id": workflow_id,
        "total_runs": total_runs,
        "recent_runs": recent_runs,
        "by_status": {s.status: s.count for s in status_counts},
    }
