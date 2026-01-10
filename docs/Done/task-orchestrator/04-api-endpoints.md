# Phase 4: Job API Endpoints

## Task 4.1: Create Job Routes

**Files:**
- Create: `services/api/app/routes/jobs.py`
- Modify: `services/api/app/main.py` (register router)

### Step 1: Create jobs router

Create `services/api/app/routes/jobs.py`:

```python
"""Job orchestration API routes."""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Job, JobTask, Agent, User
from netagent_core.job import JobSpecParser
from netagent_core.redis_events import publish_live_session_event
from ..deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# Pydantic models
class JobSubmitRequest(BaseModel):
    spec: str = Field(..., description="Job specification (markdown or natural language)")
    name: Optional[str] = Field(None, description="Optional job name override")


class JobConfirmRequest(BaseModel):
    spec_parsed: dict = Field(..., description="Edited parsed specification")


class JobResponse(BaseModel):
    id: int
    name: str
    status: str
    execution_mode: str
    batch_size: int
    validation_mode: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobDetailResponse(JobResponse):
    spec_raw: str
    spec_parsed: Optional[dict]
    delivery_config: Optional[dict]
    results: Optional[dict]
    error_summary: Optional[str]
    tasks: list


class JobTaskResponse(BaseModel):
    id: int
    sequence: int
    name: str
    description: Optional[str]
    status: str
    is_batch: bool
    agent_id: Optional[int]
    agent_name: Optional[str]
    is_ephemeral_agent: bool
    result: Optional[dict]
    error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


# Routes

@router.post("/", response_model=JobDetailResponse)
def submit_job(
    request: JobSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit a new job for orchestration."""
    parser = JobSpecParser()
    parsed = parser.parse(request.spec)

    is_natural_language = "natural_language" in parsed.raw_config
    status = "awaiting_confirmation" if is_natural_language else "pending"

    job = Job(
        name=request.name or parsed.name,
        spec_raw=request.spec,
        spec_parsed=parsed.__dict__ if not is_natural_language else None,
        status=status,
        execution_mode=parsed.execution_mode,
        batch_size=parsed.batch_size,
        on_failure=parsed.on_failure,
        retry_count=parsed.retry_count,
        validation_mode=parsed.validation_mode,
        delivery_config=parsed.delivery,
        total_tasks=len(parsed.tasks),
        created_by=current_user.id,
    )

    db.add(job)
    db.flush()

    if not is_natural_language:
        for task_spec in parsed.tasks:
            task = JobTask(
                job_id=job.id,
                sequence=task_spec.sequence,
                name=task_spec.name,
                description=task_spec.description,
                spec=task_spec.raw_spec,
                agent_name_hint=task_spec.agent_hint,
                is_batch=task_spec.is_batch,
            )
            db.add(task)

    db.commit()
    db.refresh(job)

    logger.info(f"Job {job.id} created by {current_user.email}, status: {status}")

    publish_live_session_event("job_created", {
        "job_id": job.id,
        "name": job.name,
        "status": job.status,
        "created_by": current_user.email,
    })

    return _job_to_detail_response(job)


@router.get("/", response_model=list[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List jobs with optional filtering."""
    query = db.query(Job).order_by(Job.created_at.desc())

    if status:
        query = query.filter(Job.status == status)

    if not current_user.is_admin:
        query = query.filter(Job.created_by == current_user.id)

    jobs = query.offset(offset).limit(limit).all()
    return jobs


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get job details including tasks."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return _job_to_detail_response(job)


@router.post("/{job_id}/confirm", response_model=JobDetailResponse)
def confirm_job(
    job_id: int,
    request: JobConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm and update a job's parsed specification."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "awaiting_confirmation":
        raise HTTPException(status_code=400, detail="Job not awaiting confirmation")

    if job.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    job.spec_parsed = request.spec_parsed
    job.status = "pending"

    tasks = request.spec_parsed.get("tasks", [])
    job.total_tasks = len(tasks)

    for i, task_data in enumerate(tasks):
        task = JobTask(
            job_id=job.id,
            sequence=i + 1,
            name=task_data.get("name", f"Task {i+1}"),
            description=task_data.get("description", ""),
            spec=task_data,
            agent_name_hint=task_data.get("agent_hint"),
            is_batch=task_data.get("is_batch", False),
        )
        db.add(task)

    db.commit()
    db.refresh(job)

    logger.info(f"Job {job.id} confirmed by {current_user.email}")

    return _job_to_detail_response(job)


@router.post("/{job_id}/start")
def start_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start executing a pending job."""
    from services.worker.app.tasks.job_executor import execute_job

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "pending":
        raise HTTPException(status_code=400, detail=f"Job cannot be started (status: {job.status})")

    if job.created_by != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    job.status = "queued"
    db.commit()

    execute_job.delay(job_id)

    logger.info(f"Job {job.id} queued for execution by {current_user.email}")

    return {"status": "queued", "job_id": job_id}


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a running or pending job."""
    from netagent_core.redis_events import set_cancel_flag

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in ["completed", "failed", "cancelled"]:
        raise HTTPException(status_code=400, detail=f"Job already {job.status}")

    if job.created_by != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    if job.orchestrator_session_id:
        set_cancel_flag(job.orchestrator_session_id)

    job.status = "cancelled"
    job.completed_at = datetime.utcnow()
    db.commit()

    logger.info(f"Job {job.id} cancelled by {current_user.email}")

    publish_live_session_event("job_cancelled", {
        "job_id": job.id,
        "cancelled_by": current_user.email,
    })

    return {"status": "cancelled", "job_id": job_id}


@router.post("/{job_id}/retry")
def retry_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry a failed job."""
    from services.worker.app.tasks.job_executor import execute_job

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ["failed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Only failed/cancelled jobs can be retried")

    if job.created_by != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    job.status = "queued"
    job.error_summary = None
    job.started_at = None
    job.completed_at = None
    job.completed_tasks = 0
    job.failed_tasks = 0

    for task in job.tasks:
        task.status = "pending"
        task.error = None
        task.result = None
        task.started_at = None
        task.completed_at = None

    db.commit()

    execute_job.delay(job_id)

    logger.info(f"Job {job.id} retried by {current_user.email}")

    return {"status": "queued", "job_id": job_id}


@router.delete("/{job_id}")
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a job (must be completed, failed, or cancelled)."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in ["queued", "executing", "validating", "delivering"]:
        raise HTTPException(status_code=400, detail="Cannot delete running job")

    if job.created_by != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    db.delete(job)
    db.commit()

    logger.info(f"Job {job.id} deleted by {current_user.email}")

    return {"status": "deleted", "job_id": job_id}


@router.get("/{job_id}/tasks", response_model=list[JobTaskResponse])
def list_job_tasks(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all tasks for a job."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return [_task_to_response(t) for t in sorted(job.tasks, key=lambda t: t.sequence)]


@router.get("/{job_id}/results")
def get_job_results(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get aggregated job results."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if job.status not in ["completed", "failed"]:
        raise HTTPException(status_code=400, detail="Job not yet completed")

    return {
        "job_id": job.id,
        "job_name": job.name,
        "status": job.status,
        "results": job.results,
        "error_summary": job.error_summary,
        "tasks": [
            {
                "sequence": t.sequence,
                "name": t.name,
                "status": t.status,
                "result": t.result,
                "error": t.error,
            }
            for t in sorted(job.tasks, key=lambda t: t.sequence)
        ],
    }


@router.post("/{job_id}/redeliver")
def redeliver_results(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-send job results to delivery channels."""
    from services.worker.app.tasks.job_executor import deliver_job_results

    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed jobs can be redelivered")

    if job.created_by != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    deliver_job_results.delay(job_id)

    logger.info(f"Job {job.id} results redelivery requested by {current_user.email}")

    return {"status": "redelivery_queued", "job_id": job_id}


# =============================================================================
# Bulk Operations - MUST be defined before {job_id} routes to avoid conflicts
# =============================================================================

class BulkJobsRequest(BaseModel):
    job_ids: list[int] = Field(..., description="List of job IDs to operate on")


class BulkJobsResponse(BaseModel):
    success: list[int]
    failed: list[dict]  # [{"id": 1, "error": "reason"}]


@router.post("/bulk/delete", response_model=BulkJobsResponse)
def bulk_delete_jobs(
    request: BulkJobsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete multiple jobs at once.

    Only jobs that are completed, failed, or cancelled can be deleted.
    """
    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        if not current_user.is_admin and job.created_by != current_user.id:
            failed.append({"id": job_id, "error": "Access denied"})
            continue

        if job.status in ["queued", "executing", "validating", "delivering"]:
            failed.append({"id": job_id, "error": f"Cannot delete running job (status: {job.status})"})
            continue

        db.delete(job)
        success.append(job_id)

    db.commit()
    logger.info(f"Bulk delete: {len(success)} deleted, {len(failed)} failed by {current_user.email}")

    return BulkJobsResponse(success=success, failed=failed)


@router.post("/bulk/cancel", response_model=BulkJobsResponse)
def bulk_cancel_jobs(
    request: BulkJobsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel multiple jobs at once."""
    from netagent_core.redis_events import set_cancel_flag

    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        if not current_user.is_admin and job.created_by != current_user.id:
            failed.append({"id": job_id, "error": "Access denied"})
            continue

        if job.status in ["completed", "failed", "cancelled"]:
            failed.append({"id": job_id, "error": f"Already {job.status}"})
            continue

        if job.orchestrator_session_id:
            set_cancel_flag(job.orchestrator_session_id)

        job.status = "cancelled"
        job.completed_at = datetime.utcnow()
        success.append(job_id)

    db.commit()
    logger.info(f"Bulk cancel: {len(success)} cancelled, {len(failed)} failed by {current_user.email}")

    publish_live_session_event("jobs_bulk_cancelled", {
        "job_ids": success,
        "cancelled_by": current_user.email,
    })

    return BulkJobsResponse(success=success, failed=failed)


@router.post("/bulk/retry", response_model=BulkJobsResponse)
def bulk_retry_jobs(
    request: BulkJobsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry multiple failed/cancelled jobs at once."""
    from services.worker.app.tasks.job_executor import execute_job

    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        if not current_user.is_admin and job.created_by != current_user.id:
            failed.append({"id": job_id, "error": "Access denied"})
            continue

        if job.status not in ["failed", "cancelled"]:
            failed.append({"id": job_id, "error": f"Cannot retry (status: {job.status})"})
            continue

        # Reset job state
        job.status = "queued"
        job.error_summary = None
        job.started_at = None
        job.completed_at = None
        job.completed_tasks = 0
        job.failed_tasks = 0

        for task in job.tasks:
            task.status = "pending"
            task.error = None
            task.result = None
            task.started_at = None
            task.completed_at = None

        success.append(job_id)

    db.commit()

    # Queue all for execution
    for job_id in success:
        execute_job.delay(job_id)

    logger.info(f"Bulk retry: {len(success)} queued, {len(failed)} failed by {current_user.email}")

    return BulkJobsResponse(success=success, failed=failed)


class BulkStatusUpdateRequest(BaseModel):
    job_ids: list[int]
    status: str = Field(..., description="New status (only 'pending' allowed for awaiting_confirmation jobs)")


@router.post("/bulk/status", response_model=BulkJobsResponse)
def bulk_update_status(
    request: BulkStatusUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update status for multiple jobs.

    Primary use: Confirm multiple 'awaiting_confirmation' jobs to 'pending'.
    """
    if request.status not in ["pending"]:
        raise HTTPException(status_code=400, detail="Only 'pending' status allowed for bulk update")

    success = []
    failed = []

    for job_id in request.job_ids:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            failed.append({"id": job_id, "error": "Not found"})
            continue

        if not current_user.is_admin and job.created_by != current_user.id:
            failed.append({"id": job_id, "error": "Access denied"})
            continue

        if job.status != "awaiting_confirmation":
            failed.append({"id": job_id, "error": f"Cannot update (status: {job.status})"})
            continue

        job.status = request.status
        success.append(job_id)

    db.commit()
    logger.info(f"Bulk status update to '{request.status}': {len(success)} updated by {current_user.email}")

    return BulkJobsResponse(success=success, failed=failed)


# Helper functions

def _job_to_detail_response(job: Job) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "execution_mode": job.execution_mode,
        "batch_size": job.batch_size,
        "validation_mode": job.validation_mode,
        "total_tasks": job.total_tasks,
        "completed_tasks": job.completed_tasks,
        "failed_tasks": job.failed_tasks,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "spec_raw": job.spec_raw,
        "spec_parsed": job.spec_parsed,
        "delivery_config": job.delivery_config,
        "results": job.results,
        "error_summary": job.error_summary,
        "tasks": [_task_to_response(t) for t in sorted(job.tasks, key=lambda t: t.sequence)],
    }


def _task_to_response(task: JobTask) -> dict:
    agent_name = None
    if task.agent:
        agent_name = task.agent.name
    elif task.ephemeral_agent:
        agent_name = f"[Ephemeral] {task.ephemeral_agent.name}"

    return {
        "id": task.id,
        "sequence": task.sequence,
        "name": task.name,
        "description": task.description,
        "status": task.status,
        "is_batch": task.is_batch,
        "agent_id": task.agent_id or task.ephemeral_agent_id,
        "agent_name": agent_name,
        "is_ephemeral_agent": task.is_ephemeral_agent,
        "result": task.result,
        "error": task.error,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }
```

### Step 2: Register router in main.py

Add to `services/api/app/main.py`:

```python
from .routes.jobs import router as jobs_router

# In the router registration section:
app.include_router(jobs_router, prefix="/api")
```

### Step 3: Commit

```bash
git add services/api/app/routes/jobs.py services/api/app/main.py
git commit -m "feat(api): add job orchestration endpoints"
```

---

## Verification

### 1. API Smoke Tests

```bash
TOKEN="your-auth-token"
API_URL="http://localhost:8000"

# Create job
JOB=$(curl -s -X POST "$API_URL/api/jobs/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"spec": "# Job: Test\n## Tasks\n1. **Task**\n   - Test"}')
JOB_ID=$(echo $JOB | jq -r '.id')
echo "Created job: $JOB_ID"

# List jobs
curl -s "$API_URL/api/jobs/" -H "Authorization: Bearer $TOKEN" | jq '.[0]'

# Get job
curl -s "$API_URL/api/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN" | jq '.status'

# Get tasks
curl -s "$API_URL/api/jobs/$JOB_ID/tasks" -H "Authorization: Bearer $TOKEN" | jq '.[0].name'

# Cancel and delete
curl -s -X POST "$API_URL/api/jobs/$JOB_ID/cancel" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$API_URL/api/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN"
echo "✓ All API endpoints work"
```

### 2. Check OpenAPI Docs

```bash
open http://localhost:8000/docs
# Verify /api/jobs endpoints visible
```

### 3. Bulk Operations Tests

```bash
# Create multiple test jobs
for i in 1 2 3; do
  curl -s -X POST "$API_URL/api/jobs/" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"spec\": \"# Job: Bulk Test $i\n## Tasks\n1. **Task**\n   - Test\"}"
done

# Get job IDs (adjust based on your data)
JOB_IDS=$(curl -s "$API_URL/api/jobs/?status=pending" -H "Authorization: Bearer $TOKEN" | jq '[.[].id]')
echo "Job IDs: $JOB_IDS"

# Bulk cancel
curl -s -X POST "$API_URL/api/jobs/bulk/cancel" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"job_ids\": $JOB_IDS}" | jq .

# Bulk delete (now that they're cancelled)
curl -s -X POST "$API_URL/api/jobs/bulk/delete" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"job_ids\": $JOB_IDS}" | jq .

echo "✓ Bulk operations work"
```

### Expected Outcomes

- [ ] POST /api/jobs/ creates job
- [ ] GET /api/jobs/ lists jobs
- [ ] GET /api/jobs/{id} returns details
- [ ] POST /api/jobs/{id}/start queues job
- [ ] POST /api/jobs/{id}/cancel cancels job
- [ ] DELETE /api/jobs/{id} removes job
- [ ] POST /api/jobs/bulk/delete deletes multiple jobs
- [ ] POST /api/jobs/bulk/cancel cancels multiple jobs
- [ ] POST /api/jobs/bulk/retry retries multiple jobs
- [ ] POST /api/jobs/bulk/status confirms awaiting_confirmation jobs
- [ ] Endpoints appear in Swagger docs
