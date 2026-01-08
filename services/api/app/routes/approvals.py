"""Approval management routes."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Approval, AgentSession
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType
from netagent_core.redis_events import publish_session_event, publish_live_session_event

router = APIRouter()


class ApprovalResponse(BaseModel):
    id: int
    agent_action_id: Optional[int]
    session_id: Optional[int]
    workflow_run_id: Optional[int]
    action_type: str
    action_description: str
    action_details: Optional[dict]
    risk_level: Optional[str]
    status: str
    slack_message_ts: Optional[str]
    resolved_by: Optional[int]
    resolved_at: Optional[datetime]
    resolution_note: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ApprovalAction(BaseModel):
    note: Optional[str] = None


@router.get("", response_model=dict)
async def list_approvals(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    status: Optional[str] = None,
    session_id: Optional[int] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List approvals."""
    query = db.query(Approval)

    if status:
        query = query.filter(Approval.status == status)
    if session_id:
        query = query.filter(Approval.session_id == session_id)

    total = query.count()
    approvals = query.order_by(Approval.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [ApprovalResponse.model_validate(a) for a in approvals],
        "total": total,
    }


@router.get("/pending", response_model=dict)
async def list_pending_approvals(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List pending approvals."""
    approvals = db.query(Approval).filter(
        Approval.status == "pending"
    ).order_by(Approval.created_at.desc()).all()

    return {
        "items": [ApprovalResponse.model_validate(a) for a in approvals],
        "count": len(approvals),
    }


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get approval details."""
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    return ApprovalResponse.model_validate(approval)


@router.post("/{approval_id}/approve")
async def approve_action(
    approval_id: int,
    data: ApprovalAction,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Approve an action."""
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval is already {approval.status}")

    # Check expiry
    if approval.expires_at and datetime.utcnow() > approval.expires_at:
        approval.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="Approval has expired")

    approval.status = "approved"
    approval.resolved_by = user.id
    approval.resolved_at = datetime.utcnow()
    approval.resolution_note = data.note
    db.commit()

    # Resume workflow/session if applicable
    if approval.session_id:
        session = db.query(AgentSession).filter(AgentSession.id == approval.session_id).first()
        if session and session.status == "waiting_approval":
            session.status = "active"
            db.commit()

            # Publish approval resolved event
            publish_session_event(approval.session_id, "approval_resolved", {
                "approval_id": approval.id,
                "status": "approved",
                "resolved_by": user.email,
            })
            publish_live_session_event("session_resumed", {
                "session_id": approval.session_id,
            })

    audit_log(
        db,
        AuditEventType.APPROVAL_GRANTED,
        user=user,
        resource_type="approval",
        resource_id=approval.id,
        action="approve",
        details={"action_type": approval.action_type, "note": data.note},
    )

    return {"message": "Action approved", "status": "approved"}


@router.post("/{approval_id}/reject")
async def reject_action(
    approval_id: int,
    data: ApprovalAction,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Reject an action."""
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval is already {approval.status}")

    approval.status = "rejected"
    approval.resolved_by = user.id
    approval.resolved_at = datetime.utcnow()
    approval.resolution_note = data.note
    db.commit()

    # Mark session as failed if applicable
    if approval.session_id:
        session = db.query(AgentSession).filter(AgentSession.id == approval.session_id).first()
        if session and session.status == "waiting_approval":
            session.status = "failed"
            db.commit()

            # Publish rejection event
            publish_session_event(approval.session_id, "approval_resolved", {
                "approval_id": approval.id,
                "status": "rejected",
                "resolved_by": user.email,
                "note": data.note,
            })
            publish_live_session_event("session_failed", {
                "session_id": approval.session_id,
                "reason": "Approval rejected",
            })

    audit_log(
        db,
        AuditEventType.APPROVAL_REJECTED,
        user=user,
        resource_type="approval",
        resource_id=approval.id,
        action="reject",
        details={"action_type": approval.action_type, "note": data.note},
    )

    return {"message": "Action rejected", "status": "rejected"}
