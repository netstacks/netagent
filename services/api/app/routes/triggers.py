"""Trigger routes for webhooks and scheduled runs."""

import hmac
import hashlib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Workflow, WorkflowRun
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


@router.post("/webhook/{workflow_id}")
async def webhook_trigger(
    workflow_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_signature: Optional[str] = Header(None),
):
    """Webhook endpoint to trigger a workflow.

    Validates signature using workflow's webhook_secret.
    """
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not workflow.enabled:
        raise HTTPException(status_code=400, detail="Workflow is disabled")

    if workflow.trigger_type not in ["webhook", "manual"]:
        raise HTTPException(status_code=400, detail="Workflow does not accept webhook triggers")

    # Get request body
    body = await request.body()

    # Validate signature if webhook_secret is set
    if workflow.webhook_secret:
        if not x_webhook_signature:
            raise HTTPException(status_code=401, detail="Missing webhook signature")

        expected_signature = hmac.new(
            workflow.webhook_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()

        # Support both sha256=xxx and plain xxx formats
        provided_signature = x_webhook_signature
        if provided_signature.startswith("sha256="):
            provided_signature = provided_signature[7:]

        if not hmac.compare_digest(expected_signature, provided_signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse trigger data
    import json
    try:
        trigger_data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        trigger_data = {"raw_body": body.decode("utf-8", errors="replace")}

    # Create workflow run
    run = WorkflowRun(
        workflow_id=workflow_id,
        status="pending",
        trigger_type="webhook",
        trigger_data=trigger_data,
        context={},
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    # TODO: Queue workflow execution
    # from services.tasks import execute_workflow
    # execute_workflow.delay(run.id)

    audit_log(
        db,
        AuditEventType.WORKFLOW_RUN_STARTED,
        resource_type="workflow_run",
        resource_id=run.id,
        resource_name=workflow.name,
        action="webhook_trigger",
        details={"source_ip": request.client.host if request.client else None},
    )

    return {
        "run_id": run.id,
        "status": "pending",
        "message": "Workflow triggered",
    }


@router.get("/schedules")
async def list_schedules(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List all scheduled workflow runs."""
    workflows = db.query(Workflow).filter(
        Workflow.trigger_type == "scheduled",
        Workflow.enabled == True,
        Workflow.schedule_cron.isnot(None),
    ).all()

    schedules = []
    for w in workflows:
        # TODO: Calculate next run time from cron expression
        schedules.append({
            "workflow_id": w.id,
            "workflow_name": w.name,
            "cron": w.schedule_cron,
            "enabled": w.enabled,
            "next_run": None,  # TODO: Calculate from cron
        })

    return {"schedules": schedules}


@router.post("/schedules/{workflow_id}/run")
async def trigger_scheduled_run(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Manually trigger a scheduled workflow run."""
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not workflow.enabled:
        raise HTTPException(status_code=400, detail="Workflow is disabled")

    run = WorkflowRun(
        workflow_id=workflow_id,
        status="pending",
        trigger_type="manual",
        trigger_data={},
        context={},
        initiated_by=user.id,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    # TODO: Queue workflow execution
    # from services.tasks import execute_workflow
    # execute_workflow.delay(run.id)

    audit_log(
        db,
        AuditEventType.WORKFLOW_RUN_STARTED,
        user=user,
        resource_type="workflow_run",
        resource_id=run.id,
        resource_name=workflow.name,
        action="manual_trigger",
    )

    return {
        "run_id": run.id,
        "status": "pending",
        "message": "Workflow triggered",
    }
