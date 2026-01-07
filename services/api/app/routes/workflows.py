"""Workflow management routes."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Workflow, WorkflowRun, WorkflowNodeExecution
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    definition: dict
    trigger_type: str = "manual"
    schedule_cron: Optional[str] = None
    webhook_secret: Optional[str] = None
    default_output_type: Optional[str] = None
    default_output_config: Optional[dict] = None
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    definition: Optional[dict] = None
    trigger_type: Optional[str] = None
    schedule_cron: Optional[str] = None
    default_output_type: Optional[str] = None
    default_output_config: Optional[dict] = None
    enabled: Optional[bool] = None


class WorkflowResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    definition: dict
    trigger_type: str
    schedule_cron: Optional[str]
    webhook_secret: Optional[str]
    default_output_type: Optional[str]
    default_output_config: Optional[dict]
    enabled: bool
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    status: str
    trigger_type: Optional[str]
    trigger_data: Optional[dict]
    current_node_id: Optional[str]
    context: dict
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    initiated_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class WorkflowRunCreate(BaseModel):
    trigger_data: Optional[dict] = None


@router.get("", response_model=dict)
async def list_workflows(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    trigger_type: Optional[str] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all workflows."""
    query = db.query(Workflow)

    if enabled is not None:
        query = query.filter(Workflow.enabled == enabled)
    if trigger_type:
        query = query.filter(Workflow.trigger_type == trigger_type)

    total = query.count()
    workflows = query.order_by(Workflow.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [WorkflowResponse.model_validate(w) for w in workflows],
        "total": total,
    }


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get workflow by ID."""
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return WorkflowResponse.model_validate(workflow)


@router.post("", response_model=WorkflowResponse)
async def create_workflow(
    data: WorkflowCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new workflow."""
    import secrets

    workflow = Workflow(
        name=data.name,
        description=data.description,
        definition=data.definition,
        trigger_type=data.trigger_type,
        schedule_cron=data.schedule_cron,
        webhook_secret=data.webhook_secret or secrets.token_urlsafe(32),
        default_output_type=data.default_output_type,
        default_output_config=data.default_output_config,
        enabled=data.enabled,
        created_by=user.id,
    )

    db.add(workflow)
    db.commit()
    db.refresh(workflow)

    audit_log(
        db,
        AuditEventType.WORKFLOW_CREATED,
        user=user,
        resource_type="workflow",
        resource_id=workflow.id,
        resource_name=workflow.name,
        action="create",
    )

    return WorkflowResponse.model_validate(workflow)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: int,
    data: WorkflowUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update a workflow."""
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(workflow, key, value)

    workflow.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(workflow)

    audit_log(
        db,
        AuditEventType.WORKFLOW_UPDATED,
        user=user,
        resource_type="workflow",
        resource_id=workflow.id,
        resource_name=workflow.name,
        action="update",
    )

    return WorkflowResponse.model_validate(workflow)


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete a workflow."""
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    workflow_name = workflow.name
    db.delete(workflow)
    db.commit()

    audit_log(
        db,
        AuditEventType.WORKFLOW_DELETED,
        user=user,
        resource_type="workflow",
        resource_id=workflow_id,
        resource_name=workflow_name,
        action="delete",
    )

    return {"message": "Workflow deleted"}


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(
    workflow_id: int,
    data: WorkflowRunCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Execute a workflow."""
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not workflow.enabled:
        raise HTTPException(status_code=400, detail="Workflow is disabled")

    # Create workflow run
    run = WorkflowRun(
        workflow_id=workflow_id,
        status="pending",
        trigger_type="manual",
        trigger_data=data.trigger_data or {},
        context={},
        initiated_by=user.id,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    # TODO: Queue workflow execution task
    # from services.tasks import execute_workflow
    # execute_workflow.delay(run.id)

    audit_log(
        db,
        AuditEventType.WORKFLOW_RUN_STARTED,
        user=user,
        resource_type="workflow_run",
        resource_id=run.id,
        resource_name=workflow.name,
        action="execute",
    )

    return WorkflowRunResponse.model_validate(run)


@router.get("/{workflow_id}/runs", response_model=dict)
async def list_workflow_runs(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    status: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    offset: int = 0,
):
    """List runs for a workflow."""
    query = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow_id)

    if status:
        query = query.filter(WorkflowRun.status == status)

    total = query.count()
    runs = query.order_by(WorkflowRun.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [WorkflowRunResponse.model_validate(r) for r in runs],
        "total": total,
    }


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get workflow run details."""
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    return WorkflowRunResponse.model_validate(run)


@router.get("/runs/{run_id}/trace", response_model=dict)
async def get_workflow_trace(
    run_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get execution trace for a workflow run."""
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    nodes = db.query(WorkflowNodeExecution).filter(
        WorkflowNodeExecution.workflow_run_id == run_id
    ).order_by(WorkflowNodeExecution.started_at).all()

    return {
        "run": WorkflowRunResponse.model_validate(run),
        "nodes": [
            {
                "id": n.id,
                "node_id": n.node_id,
                "node_type": n.node_type,
                "status": n.status,
                "input_data": n.input_data,
                "output_data": n.output_data,
                "started_at": n.started_at.isoformat() if n.started_at else None,
                "completed_at": n.completed_at.isoformat() if n.completed_at else None,
                "error_message": n.error_message,
            }
            for n in nodes
        ],
    }
