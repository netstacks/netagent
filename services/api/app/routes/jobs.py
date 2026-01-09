"""Job orchestration API routes."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Job, JobTask, User
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType
from netagent_core.job import JobSpecParser, NaturalLanguageConverter

logger = logging.getLogger(__name__)

router = APIRouter()


def infer_task_dependencies(tasks: list) -> list:
    """Infer dependencies between tasks based on their descriptions.

    Looks for patterns like:
    - "from Task 1", "from task 1"
    - "obtained from Task 1"
    - "from step 1", "from Step 1"
    - "results from Task 1"
    - "output of Task 1"
    - References like "Task 1", "task_1"

    Args:
        tasks: List of task dicts with sequence, name, description

    Returns:
        Updated list with depends_on populated
    """
    import re

    # Pattern to find task references in text
    # Matches: "Task 1", "task 1", "Step 1", "step 1", "task_1"
    task_ref_pattern = re.compile(
        r'(?:from\s+)?(?:task|step)[\s_]?(\d+)',
        re.IGNORECASE
    )

    for task in tasks:
        seq = task.get("sequence", 0)
        description = task.get("description") or ""
        name = task.get("name") or ""
        text = f"{name} {description}".lower()

        depends_on = task.get("depends_on", [])

        # Find all task references
        for match in task_ref_pattern.finditer(text):
            ref_seq = int(match.group(1))
            # Only add as dependency if it's a previous task
            if ref_seq < seq and ref_seq not in depends_on:
                depends_on.append(ref_seq)
                logger.info(f"Task {seq} '{task.get('name')}' inferred dependency on task {ref_seq}")

        task["depends_on"] = depends_on

    return tasks


# Pydantic models
class TaskInput(BaseModel):
    """Input model for a task in job submission."""
    name: str
    description: Optional[str] = None
    agent_hint: Optional[str] = None
    order: Optional[int] = None


class JobSubmit(BaseModel):
    """Submit a new job specification.

    Supports two modes:
    - Direct task submission: Provide 'tasks' array with task definitions
    - Spec parsing: Provide 'spec_text' to be parsed (natural language or structured)
    """
    name: str
    tasks: Optional[List[TaskInput]] = None  # Direct task submission
    spec_text: Optional[str] = None  # For backwards compatibility / spec parsing
    execution_mode: str = "batch"
    batch_size: int = 5
    on_failure: str = "continue"
    delivery_config: Optional[dict] = None
    auto_start: bool = False


class JobParse(BaseModel):
    """Parse a job specification for preview."""
    spec_text: str


class ParsedTaskResponse(BaseModel):
    """Response model for a parsed task."""
    sequence: int
    name: str
    description: Optional[str]
    agent_hint: Optional[str]
    is_batch: bool


class ParsedSpecResponse(BaseModel):
    """Response model for parsed specification."""
    name: str
    tasks: List[ParsedTaskResponse]
    config: Optional[dict]
    delivery: Optional[dict]


class JobConfirm(BaseModel):
    """Confirm a natural language job with parsed spec."""
    spec_parsed: dict


class TaskResponse(BaseModel):
    """Response model for a job task."""
    id: int
    sequence: int
    name: str
    description: Optional[str] = None
    status: str
    agent_id: Optional[int] = None
    agent_name_hint: Optional[str] = None
    is_batch: bool = False
    depends_on: Optional[List[int]] = None
    batch_source_task: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_safe(cls, obj):
        """Create TaskResponse from ORM object, handling missing columns."""
        data = {
            "id": obj.id,
            "sequence": obj.sequence,
            "name": obj.name,
            "description": getattr(obj, "description", None),
            "status": obj.status,
            "agent_id": getattr(obj, "agent_id", None),
            "agent_name_hint": getattr(obj, "agent_name_hint", None),
            "is_batch": getattr(obj, "is_batch", False),
            "depends_on": getattr(obj, "depends_on", None),
            "batch_source_task": getattr(obj, "batch_source_task", None),
            "started_at": getattr(obj, "started_at", None),
            "completed_at": getattr(obj, "completed_at", None),
            "result": getattr(obj, "result", None),
            "error": getattr(obj, "error", None),
        }
        return cls(**data)


class JobResponse(BaseModel):
    """Response model for a job."""
    id: int
    name: str
    status: str
    execution_mode: str
    batch_size: int
    on_failure: str
    validation_mode: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    created_by: Optional[int]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_summary: Optional[str]

    class Config:
        from_attributes = True


class JobDetailResponse(JobResponse):
    """Detailed response including tasks."""
    spec_raw: str
    spec_parsed: Optional[dict]
    delivery_config: Optional[dict]
    results: Optional[dict]
    tasks: List[TaskResponse]


@router.post("/parse", response_model=ParsedSpecResponse)
async def parse_job_spec(
    data: JobParse,
    user: ALBUser = Depends(get_current_user),
):
    """Parse a job specification for preview.

    This is used by the UI to show a preview of tasks before submitting.
    Supports both structured markdown and natural language input.
    Natural language is converted to structured format using AI.
    """
    parser = JobSpecParser()
    parsed = parser.parse(data.spec_text)

    # If natural language detected (no tasks parsed), use AI to convert
    if not parsed.tasks and "natural_language" in parsed.raw_config:
        logger.info("Natural language input detected, converting with AI...")
        try:
            converter = NaturalLanguageConverter()
            parsed = converter.convert(data.spec_text)
            logger.info(f"Converted natural language to {len(parsed.tasks)} tasks")
        except Exception as e:
            logger.error(f"Natural language conversion failed: {e}")
            # Return empty tasks, let UI show the error

    tasks = [
        ParsedTaskResponse(
            sequence=task.sequence,
            name=task.name,
            description=task.description,
            agent_hint=task.agent_hint,
            is_batch=task.is_batch,
        )
        for task in parsed.tasks
    ]

    return ParsedSpecResponse(
        name=parsed.name,
        tasks=tasks,
        config={
            "mode": parsed.execution_mode,
            "batch_size": parsed.batch_size,
            "on_failure": parsed.on_failure,
            "validation_mode": parsed.validation_mode,
        },
        delivery=parsed.delivery,
    )


@router.post("/", response_model=JobResponse)
async def submit_job(
    data: JobSubmit,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Submit a new job.

    Supports two modes:
    1. Direct task submission: Provide 'tasks' array with task definitions
       - Tasks are created immediately, job goes to 'pending' status
    2. Spec parsing: Provide 'spec_text' to be parsed
       - Specification is parsed and tasks are created, job goes to 'pending' status

    All jobs go directly to 'pending' status - there is no confirmation step.
    """
    # Get user from database
    db_user = db.query(User).filter(User.email == user.email).first()
    user_id = db_user.id if db_user else None

    tasks_to_create = []

    # Mode 1: Direct task submission (from new UI)
    if data.tasks and len(data.tasks) > 0:
        for i, task in enumerate(data.tasks):
            tasks_to_create.append({
                "sequence": task.order or (i + 1),
                "name": task.name,
                "description": task.description,
                "agent_hint": task.agent_hint,
                "is_batch": False,
                "depends_on": [],  # Will be inferred below
            })

        # Infer dependencies from task descriptions (e.g., "from Task 1")
        tasks_to_create = infer_task_dependencies(tasks_to_create)

    # Mode 2: Parse spec text (for backwards compatibility or natural language)
    elif data.spec_text:
        parser = JobSpecParser()
        parsed = parser.parse(data.spec_text)

        # If natural language (no structured tasks), use AI to convert
        if not parsed.tasks and "natural_language" in parsed.raw_config:
            try:
                converter = NaturalLanguageConverter()
                parsed = converter.convert(data.spec_text)
            except Exception as e:
                logger.error(f"Natural language conversion failed: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse natural language: {str(e)}"
                )

        for task in parsed.tasks:
            tasks_to_create.append({
                "sequence": task.sequence,
                "name": task.name,
                "description": task.description,
                "agent_hint": task.agent_hint,
                "is_batch": task.is_batch,
                "raw_spec": task.raw_spec,
            })
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'tasks' array or 'spec_text' must be provided"
        )

    if not tasks_to_create:
        raise HTTPException(
            status_code=400,
            detail="No tasks could be created from the provided input"
        )

    # Create the job - always starts in 'pending' status
    job = Job(
        name=data.name,
        spec_raw=data.spec_text or "",
        spec_parsed=None,
        status="pending",
        execution_mode=data.execution_mode,
        batch_size=data.batch_size,
        validation_mode="none",
        on_failure=data.on_failure,
        retry_count=0,
        delivery_config=data.delivery_config,
        total_tasks=len(tasks_to_create),
        created_by=user_id,
    )

    db.add(job)
    db.flush()

    # Create all tasks
    for task_data in tasks_to_create:
        # Build spec with all task metadata including dependencies
        spec = task_data.get("raw_spec", {})
        spec["depends_on"] = task_data.get("depends_on", [])
        if task_data.get("batch_source"):
            spec["batch_source"] = task_data["batch_source"]

        job_task = JobTask(
            job_id=job.id,
            sequence=task_data["sequence"],
            name=task_data["name"],
            description=task_data.get("description"),
            spec=spec,
            agent_name_hint=task_data.get("agent_hint"),
            is_batch=task_data.get("is_batch", False),
            depends_on=task_data.get("depends_on", []),
            batch_source_task=int(task_data["batch_source"].split("_")[1]) if task_data.get("batch_source") else None,
        )
        db.add(job_task)

    db.commit()
    db.refresh(job)

    # Audit log
    audit_log(
        db,
        AuditEventType.JOB_CREATED,
        user=user,
        resource_type="job",
        resource_id=job.id,
        resource_name=job.name,
        details={"status": "pending", "task_count": len(tasks_to_create)},
    )

    logger.info(f"Job {job.id} '{job.name}' created by {user.email} with {len(tasks_to_create)} tasks")

    # Auto-start if requested
    if data.auto_start:
        job.status = "queued"
        job.started_at = datetime.utcnow()
        db.commit()

        from celery_config import send_task
        send_task('tasks.job_executor.execute_job', args=[job.id])
        logger.info(f"Job {job.id} auto-started")

    return job


@router.get("/", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """List jobs, optionally filtered by status."""
    query = db.query(Job).order_by(Job.created_at.desc())

    if status:
        query = query.filter(Job.status == status)

    jobs = query.offset(offset).limit(limit).all()
    return jobs


# =============================================================================
# Bulk Operations - MUST be defined BEFORE {job_id} routes to avoid conflicts
# =============================================================================

class BulkJobsRequest(BaseModel):
    """Request for bulk job operations."""
    job_ids: List[int] = Field(..., description="List of job IDs to operate on")


class BulkJobsResponse(BaseModel):
    """Response for bulk job operations."""
    success: List[int]
    failed: List[dict]  # [{"id": 1, "error": "reason"}]


@router.post("/bulk/delete", response_model=BulkJobsResponse)
async def bulk_delete_jobs(
    request: BulkJobsRequest,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete multiple jobs at once.

    Only jobs in terminal states (completed, failed, cancelled) can be deleted.
    Ephemeral agents and their sessions created for these jobs will be deleted first.
    """
    from netagent_core.db import Agent, AgentSession

    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        # Allow deletion of terminal states only
        if job.status not in ["completed", "failed", "cancelled"]:
            failed.append({"id": job_id, "error": f"Cannot delete running job (status: {job.status})"})
            continue

        try:
            # Find ephemeral agent IDs for this job
            ephemeral_agent_ids = [
                agent_id for (agent_id,) in db.query(Agent.id).filter(
                    Agent.is_ephemeral == True,
                    Agent.created_for_job_id == job_id,
                ).all()
            ]

            if ephemeral_agent_ids:
                # Delete sessions for these agents first (FK constraint)
                db.query(AgentSession).filter(
                    AgentSession.agent_id.in_(ephemeral_agent_ids)
                ).delete(synchronize_session=False)

                # Delete ephemeral agents
                db.query(Agent).filter(
                    Agent.id.in_(ephemeral_agent_ids)
                ).delete(synchronize_session=False)

            db.delete(job)
            db.commit()
            success.append(job_id)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to delete job {job_id}: {e}")
            failed.append({"id": job_id, "error": str(e)})

    logger.info(f"Bulk delete: {len(success)} deleted, {len(failed)} failed by {user.email}")

    return BulkJobsResponse(success=success, failed=failed)


@router.post("/bulk/cancel", response_model=BulkJobsResponse)
async def bulk_cancel_jobs(
    request: BulkJobsRequest,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Cancel multiple jobs at once.

    Jobs that are already in terminal states (completed, failed, cancelled) will be skipped.
    """
    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        if job.status in ["completed", "failed", "cancelled"]:
            failed.append({"id": job_id, "error": f"Already {job.status}"})
            continue

        job.status = "cancelled"
        job.completed_at = datetime.utcnow()
        success.append(job_id)

    db.commit()
    logger.info(f"Bulk cancel: {len(success)} cancelled, {len(failed)} failed by {user.email}")

    return BulkJobsResponse(success=success, failed=failed)


# =============================================================================
# Individual Job Routes (MUST come after /bulk/* routes)
# =============================================================================

@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get job details including tasks."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job


@router.put("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: int,
    data: JobSubmit,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update a pending job.

    Only jobs in 'pending' status can be updated.
    This replaces all tasks with the new task list.
    """
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Only pending jobs can be edited - current status: {job.status}"
        )

    # Build new task list
    tasks_to_create = []

    if data.tasks and len(data.tasks) > 0:
        for i, task in enumerate(data.tasks):
            tasks_to_create.append({
                "sequence": task.order or (i + 1),
                "name": task.name,
                "description": task.description,
                "agent_hint": task.agent_hint,
                "depends_on": [],  # Will be inferred below
            })

        # Infer dependencies from task descriptions (e.g., "from Task 1")
        tasks_to_create = infer_task_dependencies(tasks_to_create)

    elif data.spec_text:
        parser = JobSpecParser()
        parsed = parser.parse(data.spec_text)

        if not parsed.tasks and "natural_language" in parsed.raw_config:
            try:
                converter = NaturalLanguageConverter()
                parsed = converter.convert(data.spec_text)
            except Exception as e:
                logger.error(f"Natural language conversion failed: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse natural language: {str(e)}"
                )

        for task in parsed.tasks:
            tasks_to_create.append({
                "sequence": task.sequence,
                "name": task.name,
                "description": task.description,
                "agent_hint": task.agent_hint,
            })
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'tasks' array or 'spec_text' must be provided"
        )

    if not tasks_to_create:
        raise HTTPException(
            status_code=400,
            detail="No tasks could be created from the provided input"
        )

    # Update job fields
    job.name = data.name
    job.spec_raw = data.spec_text or ""
    job.execution_mode = data.execution_mode
    job.batch_size = data.batch_size
    job.on_failure = data.on_failure
    job.delivery_config = data.delivery_config
    job.total_tasks = len(tasks_to_create)

    # Delete existing tasks and create new ones
    for existing_task in job.tasks:
        db.delete(existing_task)

    for task_data in tasks_to_create:
        # Build spec with all task metadata including dependencies
        spec = task_data.get("raw_spec", {})
        spec["depends_on"] = task_data.get("depends_on", [])
        if task_data.get("batch_source"):
            spec["batch_source"] = task_data["batch_source"]

        job_task = JobTask(
            job_id=job.id,
            sequence=task_data["sequence"],
            name=task_data["name"],
            description=task_data.get("description"),
            spec=spec,
            agent_name_hint=task_data.get("agent_hint"),
            is_batch=task_data.get("is_batch", False),
            depends_on=task_data.get("depends_on", []),
            batch_source_task=int(task_data["batch_source"].split("_")[1]) if task_data.get("batch_source") else None,
        )
        db.add(job_task)

    db.commit()
    db.refresh(job)

    logger.info(f"Job {job.id} updated by {user.email} with {len(tasks_to_create)} tasks")

    # Auto-start if requested
    if data.auto_start:
        job.status = "queued"
        job.started_at = datetime.utcnow()
        db.commit()

        from celery_config import send_task
        send_task('tasks.job_executor.execute_job', args=[job.id])
        logger.info(f"Job {job.id} auto-started after update")

    return job


@router.get("/{job_id}/tasks", response_model=List[TaskResponse])
async def get_job_tasks(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get tasks for a job."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job.tasks


@router.post("/{job_id}/confirm", response_model=JobResponse, deprecated=True)
async def confirm_job(
    job_id: int,
    data: JobConfirm,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """DEPRECATED: This endpoint is no longer used.

    Jobs now go directly to 'pending' status with tasks created on submit.
    This endpoint is kept for backwards compatibility but will be removed in a future version.
    """
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated. Jobs are now created with tasks directly - no confirmation step needed."
    )


@router.post("/{job_id}/start", response_model=dict)
async def start_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Start executing a pending job."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be started - current status: {job.status}"
        )

    # Queue the job for execution
    job.status = "queued"
    job.started_at = datetime.utcnow()
    db.commit()

    # Dispatch to Celery
    from celery_config import send_task
    send_task('tasks.job_executor.execute_job', args=[job.id])
    logger.info(f"Job {job.id} queued for execution")

    return {"status": "queued", "job_id": job.id}


@router.post("/{job_id}/cancel", response_model=dict)
async def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Cancel a pending, queued, or running job."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ["pending", "queued", "executing", "awaiting_approval"]:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be cancelled - current status: {job.status}"
        )

    job.status = "cancelled"
    job.completed_at = datetime.utcnow()
    db.commit()

    # TODO: Signal cancellation to running tasks via Redis

    logger.info(f"Job {job.id} cancelled by {user.email}")

    return {"status": "cancelled", "job_id": job.id}


@router.post("/{job_id}/retry", response_model=dict)
async def retry_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Retry a failed or cancelled job (re-runs only failed/skipped tasks)."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ["failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be retried - current status: {job.status}"
        )

    # Reset job state
    job.status = "queued"
    job.error_summary = None
    job.completed_at = None
    job.started_at = datetime.utcnow()
    job.completed_tasks = 0
    job.failed_tasks = 0

    # Reset task states - only failed/skipped tasks
    for task in job.tasks:
        if task.status in ["failed", "skipped"]:
            task.status = "pending"
            task.error = None
            task.result = None
            task.started_at = None
            task.completed_at = None
            task.session_id = None

    db.commit()

    # Dispatch to Celery
    from celery_config import send_task
    send_task('tasks.job_executor.execute_job', args=[job.id])

    logger.info(f"Job {job.id} queued for retry by {user.email}")

    return {"status": "queued", "job_id": job.id}


@router.post("/{job_id}/rerun", response_model=dict)
async def rerun_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Rerun a completed job (re-runs all tasks from scratch)."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ["completed", "failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be rerun - current status: {job.status}"
        )

    # Reset job state completely
    job.status = "queued"
    job.error_summary = None
    job.results = None
    job.completed_at = None
    job.started_at = datetime.utcnow()
    job.completed_tasks = 0
    job.failed_tasks = 0

    # Reset ALL task states
    for task in job.tasks:
        task.status = "pending"
        task.error = None
        task.result = None
        task.started_at = None
        task.completed_at = None
        task.session_id = None
        task.agent_id = None
        task.is_ephemeral_agent = False
        task.ephemeral_agent_id = None
        task.ephemeral_prompt = None

    db.commit()

    # Dispatch to Celery
    from celery_config import send_task
    send_task('tasks.job_executor.execute_job', args=[job.id])

    logger.info(f"Job {job.id} queued for rerun by {user.email}")

    return {"status": "queued", "job_id": job.id}


@router.get("/{job_id}/results")
async def get_job_results(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get aggregated results for a completed job."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Compile task results
    task_results = []
    for task in job.tasks:
        task_results.append({
            "sequence": task.sequence,
            "name": task.name,
            "status": task.status,
            "result": task.result,
            "batch_results": task.batch_results,
            "error": task.error,
        })

    return {
        "job_id": job.id,
        "job_name": job.name,
        "status": job.status,
        "results": job.results,
        "tasks": task_results,
        "completed_at": job.completed_at,
    }


@router.post("/{job_id}/redeliver", response_model=dict)
async def redeliver_results(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Re-send results to configured delivery channels."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Only completed jobs can be redelivered - current status: {job.status}"
        )

    # Dispatch delivery task
    from celery_config import send_task
    send_task('tasks.job_executor.deliver_job_results', args=[job.id])
    logger.info(f"Job {job.id} results queued for redelivery")

    return {"status": "redelivery_queued", "job_id": job.id}


@router.get("/{job_id}/orchestration")
async def get_job_orchestration(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get orchestration details for a job including agent actions and thoughts.

    Returns detailed logs of what each agent did, their reasoning, and tool calls.
    """
    from netagent_core.db import AgentSession, AgentAction, AgentMessage, Agent

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get all sessions for this job's tasks
    task_sessions = []
    for task in job.tasks:
        if task.session_id:
            session = db.query(AgentSession).filter(AgentSession.id == task.session_id).first()
            if session:
                # Get agent info
                agent = db.query(Agent).filter(Agent.id == session.agent_id).first()

                # Get actions (tool calls, reasoning)
                actions = db.query(AgentAction).filter(
                    AgentAction.session_id == session.id
                ).order_by(AgentAction.created_at).all()

                # Get messages
                messages = db.query(AgentMessage).filter(
                    AgentMessage.session_id == session.id
                ).order_by(AgentMessage.created_at).all()

                task_sessions.append({
                    "task_sequence": task.sequence,
                    "task_name": task.name,
                    "task_status": task.status,
                    "session_id": session.id,
                    "agent_id": session.agent_id,
                    "agent_name": agent.name if agent else "Unknown",
                    "agent_is_ephemeral": agent.is_ephemeral if agent else False,
                    "session_status": session.status,
                    "started_at": session.created_at.isoformat() if session.created_at else None,
                    "completed_at": session.completed_at.isoformat() if session.completed_at else None,
                    "actions": [
                        {
                            "id": a.id,
                            "type": a.action_type,
                            "tool_name": a.tool_name,
                            "tool_input": a.tool_input,
                            "tool_output": a.tool_output,
                            "reasoning": a.reasoning,
                            "risk_level": a.risk_level,
                            "requires_approval": a.requires_approval,
                            "status": a.status,
                            "error": a.error_message,
                            "duration_ms": a.duration_ms,
                            "created_at": a.created_at.isoformat() if a.created_at else None,
                        }
                        for a in actions
                    ],
                    "messages": [
                        {
                            "id": m.id,
                            "role": m.role,
                            "content": m.content[:2000] if m.content else None,  # Truncate long content
                            "tool_calls": m.tool_calls,
                            "token_count": m.token_count,
                            "created_at": m.created_at.isoformat() if m.created_at else None,
                        }
                        for m in messages
                    ],
                })

    return {
        "job_id": job.id,
        "job_name": job.name,
        "job_status": job.status,
        "execution_mode": job.execution_mode,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "task_sessions": task_sessions,
    }


@router.delete("/{job_id}", response_model=dict)
async def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete a job (only terminal states: completed, failed, cancelled)."""
    from netagent_core.db import Agent, AgentSession

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Allow deletion of terminal states only
    if job.status not in ["completed", "failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail=f"Running jobs cannot be deleted - current status: {job.status}"
        )

    # Find ephemeral agent IDs for this job
    ephemeral_agent_ids = [
        agent_id for (agent_id,) in db.query(Agent.id).filter(
            Agent.is_ephemeral == True,
            Agent.created_for_job_id == job_id,
        ).all()
    ]

    if ephemeral_agent_ids:
        # Delete sessions for these agents first (FK constraint)
        db.query(AgentSession).filter(
            AgentSession.agent_id.in_(ephemeral_agent_ids)
        ).delete(synchronize_session=False)

        # Delete ephemeral agents
        deleted_agents = db.query(Agent).filter(
            Agent.id.in_(ephemeral_agent_ids)
        ).delete(synchronize_session=False)

        logger.info(f"Deleted {deleted_agents} ephemeral agents for job {job_id}")

    db.delete(job)
    db.commit()

    logger.info(f"Job {job.id} deleted by {user.email}")

    return {"status": "deleted", "job_id": job_id}
