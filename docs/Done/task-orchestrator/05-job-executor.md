# Phase 5: Job Executor Celery Task

## Task 5.1: Create Job Executor

**Files:**
- Create: `services/worker/app/tasks/job_executor.py`
- Modify: `services/worker/app/tasks/__init__.py` (export task)

### Step 1: Create the job executor task

Create `services/worker/app/tasks/job_executor.py`:

```python
"""Celery task for job orchestration.

Executes multi-step jobs by:
1. Parsing the job specification
2. Matching tasks to agents (or creating ephemeral agents)
3. Executing tasks in configured order (sequential, parallel, batch)
4. Aggregating results
5. Running validation
6. Delivering results
7. Cleaning up ephemeral resources
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional
from celery import shared_task
from concurrent.futures import ThreadPoolExecutor, as_completed

from netagent_core.db import (
    get_db_context,
    Job,
    JobTask,
    Agent,
    AgentSession,
    User,
)
from netagent_core.job import AgentMatcher
from netagent_core.redis_events import (
    check_cancel_flag,
    publish_live_session_event,
    set_cancel_flag,
)
from .agent_executor import execute_agent_session

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when a job is cancelled."""
    pass


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def execute_job(self, job_id: int):
    """Execute a job orchestration workflow."""
    logger.info(f"Starting job execution: {job_id}")

    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        if job.status not in ["pending", "queued"]:
            logger.warning(f"Job {job_id} has unexpected status: {job.status}")
            return {"error": f"Invalid status: {job.status}"}

        job.status = "executing"
        job.started_at = datetime.utcnow()
        db.commit()

        publish_live_session_event("job_started", {
            "job_id": job.id,
            "name": job.name,
        })

    try:
        result = _execute_job_workflow(job_id)

        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            job.results = result
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            db.commit()

        deliver_job_results.delay(job_id)
        cleanup_job_resources.delay(job_id)

        publish_live_session_event("job_completed", {
            "job_id": job_id,
            "status": "completed",
        })

        logger.info(f"Job {job_id} completed successfully")
        return result

    except JobCancelled:
        logger.info(f"Job {job_id} was cancelled")
        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            job.status = "cancelled"
            job.completed_at = datetime.utcnow()
            db.commit()
        return {"status": "cancelled"}

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            job.status = "failed"
            job.error_summary = str(e)
            job.completed_at = datetime.utcnow()
            db.commit()

        publish_live_session_event("job_failed", {
            "job_id": job_id,
            "error": str(e),
        })

        raise


def _execute_job_workflow(job_id: int) -> dict:
    """Execute the job workflow."""
    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()
        tasks = sorted(job.tasks, key=lambda t: t.sequence)
        execution_mode = job.execution_mode
        batch_size = job.batch_size
        on_failure = job.on_failure

        job_context = {
            "job_id": job.id,
            "job_name": job.name,
        }

    results = {}
    previous_results = {}

    for task in tasks:
        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job.orchestrator_session_id and check_cancel_flag(job.orchestrator_session_id):
                raise JobCancelled()

        logger.info(f"Executing task {task.sequence}: {task.name}")

        try:
            task_result = _execute_task(
                job_id=job_id,
                task_id=task.id,
                job_context=job_context,
                previous_results=previous_results,
                execution_mode=execution_mode,
                batch_size=batch_size,
            )

            results[f"task_{task.sequence}"] = task_result
            previous_results[f"task_{task.sequence}"] = task_result

            with get_db_context() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                job.completed_tasks += 1
                db.commit()

        except Exception as e:
            logger.error(f"Task {task.sequence} failed: {e}")

            with get_db_context() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                job.failed_tasks += 1

                task_obj = db.query(JobTask).filter(JobTask.id == task.id).first()
                task_obj.status = "failed"
                task_obj.error = str(e)
                task_obj.completed_at = datetime.utcnow()
                db.commit()

            results[f"task_{task.sequence}"] = {"error": str(e)}

            if on_failure == "stop":
                raise

    return results


def _execute_task(
    job_id: int,
    task_id: int,
    job_context: dict,
    previous_results: dict,
    execution_mode: str,
    batch_size: int,
) -> dict:
    """Execute a single job task."""
    with get_db_context() as db:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()

        task.status = "running"
        task.started_at = datetime.utcnow()
        db.commit()

        matcher = AgentMatcher(db)
        agent, score, reason = matcher.find_best_agent(
            task_name=task.name,
            task_description=task.description or "",
            agent_hint=task.agent_name_hint,
        )

        if agent:
            logger.info(f"Using existing agent: {agent.name} (score: {score:.2f}, {reason})")
            task.agent_id = agent.id
        else:
            logger.info(f"Creating ephemeral agent for task: {task.name}")

            ephemeral_prompt = matcher.generate_ephemeral_prompt(
                task_name=task.name,
                task_description=task.description or "",
                job_context=job_context,
            )

            job = db.query(Job).filter(Job.id == job_id).first()

            agent = Agent(
                name=f"[Job {job_id}] {task.name[:50]}",
                description=f"Ephemeral agent for job task: {task.name}",
                agent_type="ephemeral",
                system_prompt=ephemeral_prompt,
                allowed_tools=["ssh_command", "search_knowledge", "send_email"],
                is_ephemeral=True,
                created_for_job_id=job_id,
                enabled=True,
                created_by=job.created_by,
            )
            db.add(agent)
            db.flush()

            task.is_ephemeral_agent = True
            task.ephemeral_agent_id = agent.id
            task.ephemeral_prompt = ephemeral_prompt

        db.commit()

        agent_id = task.agent_id or task.ephemeral_agent_id
        is_batch = task.is_batch

    prompt = _build_task_prompt(task_id, previous_results)

    if is_batch:
        result = _execute_batch_task(
            job_id=job_id,
            task_id=task_id,
            agent_id=agent_id,
            prompt=prompt,
            execution_mode=execution_mode,
            batch_size=batch_size,
            previous_results=previous_results,
        )
    else:
        result = _execute_single_task(
            job_id=job_id,
            task_id=task_id,
            agent_id=agent_id,
            prompt=prompt,
        )

    with get_db_context() as db:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()
        task.result = result
        task.status = "completed"
        task.completed_at = datetime.utcnow()
        db.commit()

    return result


def _build_task_prompt(task_id: int, previous_results: dict) -> str:
    """Build the prompt for a task execution."""
    with get_db_context() as db:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()

        prompt_parts = [f"Execute the following task: {task.name}"]

        if task.description:
            prompt_parts.append(f"\nInstructions:\n{task.description}")

        if task.spec:
            prompt_parts.append(f"\nTask specification:\n{task.spec}")

        if previous_results:
            prompt_parts.append("\n\nContext from previous tasks:")
            for key, value in previous_results.items():
                value_str = str(value)
                if len(value_str) > 1000:
                    value_str = value_str[:1000] + "... (truncated)"
                prompt_parts.append(f"\n{key}: {value_str}")

        return "\n".join(prompt_parts)


def _execute_single_task(
    job_id: int,
    task_id: int,
    agent_id: int,
    prompt: str,
) -> dict:
    """Execute a single (non-batch) task."""
    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()

        session = AgentSession(
            agent_id=agent_id,
            status="pending",
            trigger_type="job",
            user_id=job.created_by,
            context={"job_id": job_id, "task_id": task_id},
        )
        db.add(session)
        db.flush()

        task = db.query(JobTask).filter(JobTask.id == task_id).first()
        task.session_id = session.id
        db.commit()

        session_id = session.id

    result = execute_agent_session(session_id, initial_message=prompt)

    return result


def _execute_batch_task(
    job_id: int,
    task_id: int,
    agent_id: int,
    prompt: str,
    execution_mode: str,
    batch_size: int,
    previous_results: dict,
) -> dict:
    """Execute a batch task across multiple items."""
    with get_db_context() as db:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()
        batch_source = task.spec.get("batch_source") if task.spec else None

    batch_items = []

    if batch_source and batch_source in previous_results:
        prev_result = previous_results[batch_source]
        if isinstance(prev_result, list):
            batch_items = prev_result
        elif isinstance(prev_result, dict) and "items" in prev_result:
            batch_items = prev_result["items"]
        elif isinstance(prev_result, dict) and "devices" in prev_result:
            batch_items = prev_result["devices"]

    if not batch_items:
        logger.warning(f"No batch items found for task {task_id}")
        return {"warning": "No batch items found", "items_processed": 0}

    logger.info(f"Processing {len(batch_items)} batch items")

    results = []
    errors = []

    if execution_mode == "sequential":
        for item in batch_items:
            try:
                item_prompt = f"{prompt}\n\nProcess this specific item: {item}"
                result = _execute_single_task(job_id, task_id, agent_id, item_prompt)
                results.append({"item": item, "result": result})
            except Exception as e:
                errors.append({"item": item, "error": str(e)})

    elif execution_mode == "parallel":
        with ThreadPoolExecutor(max_workers=min(len(batch_items), 10)) as executor:
            futures = {}
            for item in batch_items:
                item_prompt = f"{prompt}\n\nProcess this specific item: {item}"
                future = executor.submit(_execute_single_task, job_id, task_id, agent_id, item_prompt)
                futures[future] = item

            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                    results.append({"item": item, "result": result})
                except Exception as e:
                    errors.append({"item": item, "error": str(e)})

    else:  # batch mode (default)
        for i in range(0, len(batch_items), batch_size):
            batch = batch_items[i:i + batch_size]

            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {}
                for item in batch:
                    item_prompt = f"{prompt}\n\nProcess this specific item: {item}"
                    future = executor.submit(_execute_single_task, job_id, task_id, agent_id, item_prompt)
                    futures[future] = item

                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        result = future.result()
                        results.append({"item": item, "result": result})
                    except Exception as e:
                        errors.append({"item": item, "error": str(e)})

            time.sleep(1)

    with get_db_context() as db:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()
        task.batch_results = {"results": results, "errors": errors}
        db.commit()

    return {
        "items_processed": len(results),
        "items_failed": len(errors),
        "results": results,
        "errors": errors,
    }


@shared_task
def deliver_job_results(job_id: int):
    """Deliver job results to configured channels."""
    from .notifications import send_email, send_slack_message

    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            logger.error(f"Job {job_id} not found for delivery")
            return

        if not job.delivery_config:
            logger.info(f"No delivery config for job {job_id}")
            return

        results = job.results or {}
        job_name = job.name
        status = job.status

        creator = db.query(User).filter(User.id == job.created_by).first()
        creator_email = creator.email if creator else "unknown"

    results_text = _format_results_text(job_id, job_name, status, results)
    results_html = _format_results_html(job_id, job_name, status, results)

    delivery = job.delivery_config

    if delivery.get("email"):
        for email in delivery["email"]:
            send_email.delay(
                to=[email],
                subject=f"[NetAgent Job] {job_name} - {status.upper()}",
                body=results_text,
                html_body=results_html,
            )
            logger.info(f"Queued email delivery to {email}")

    if delivery.get("slack"):
        for channel in delivery["slack"]:
            send_slack_message.delay(
                channel=channel,
                text=f"Job completed: {job_name}",
                blocks=_format_slack_blocks(job_id, job_name, status, results),
            )
            logger.info(f"Queued Slack delivery to {channel}")

    if delivery.get("webhook"):
        for url in delivery["webhook"]:
            _deliver_to_webhook(url, job_id, job_name, status, results)

    logger.info(f"Job {job_id} results delivered")


def _format_results_text(job_id: int, job_name: str, status: str, results: dict) -> str:
    lines = [
        f"Job: {job_name}",
        f"Status: {status}",
        f"Job ID: {job_id}",
        "",
        "Results:",
        "-" * 40,
    ]

    for task_key, task_result in results.items():
        lines.append(f"\n{task_key}:")
        if isinstance(task_result, dict):
            for k, v in task_result.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {task_result}")

    return "\n".join(lines)


def _format_results_html(job_id: int, job_name: str, status: str, results: dict) -> str:
    html = f"""
    <html>
    <body>
    <h2>Job: {job_name}</h2>
    <p><strong>Status:</strong> {status}</p>
    <p><strong>Job ID:</strong> {job_id}</p>
    <h3>Results</h3>
    <hr>
    """

    for task_key, task_result in results.items():
        html += f"<h4>{task_key}</h4>"
        if isinstance(task_result, dict):
            html += "<ul>"
            for k, v in task_result.items():
                html += f"<li><strong>{k}:</strong> {v}</li>"
            html += "</ul>"
        else:
            html += f"<p>{task_result}</p>"

    html += "</body></html>"
    return html


def _format_slack_blocks(job_id: int, job_name: str, status: str, results: dict) -> list:
    status_emoji = ":white_check_mark:" if status == "completed" else ":x:"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{status_emoji} Job Completed: {job_name}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status:* {status}"},
                {"type": "mrkdwn", "text": f"*Job ID:* {job_id}"},
            ]
        },
        {"type": "divider"},
    ]

    for task_key, task_result in results.items():
        if isinstance(task_result, dict):
            summary = ", ".join([f"{k}: {v}" for k, v in list(task_result.items())[:3]])
        else:
            summary = str(task_result)[:200]

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{task_key}:* {summary}"}
        })

    return blocks


def _deliver_to_webhook(url: str, job_id: int, job_name: str, status: str, results: dict):
    import httpx

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, json={
                "job_id": job_id,
                "job_name": job_name,
                "status": status,
                "results": results,
            })
            response.raise_for_status()
            logger.info(f"Webhook delivery to {url} succeeded")
    except Exception as e:
        logger.error(f"Webhook delivery to {url} failed: {e}")


@shared_task
def cleanup_job_resources(job_id: int):
    """Clean up ephemeral agents and resources after job completion."""
    logger.info(f"Cleaning up resources for job {job_id}")

    with get_db_context() as db:
        ephemeral_agents = db.query(Agent).filter(
            Agent.is_ephemeral == True,
            Agent.created_for_job_id == job_id,
        ).all()

        for agent in ephemeral_agents:
            logger.info(f"Deleting ephemeral agent: {agent.name} (ID: {agent.id})")
            db.delete(agent)

        db.commit()

    logger.info(f"Cleanup complete for job {job_id}")
```

### Step 2: Export the task

Update `services/worker/app/tasks/__init__.py`:

```python
from .job_executor import execute_job, deliver_job_results, cleanup_job_resources
```

### Step 3: Commit

```bash
git add services/worker/app/tasks/
git commit -m "feat(worker): add job orchestration Celery task"
```

---

## Verification

### 1. Test Task Import

```bash
python3 -c "
from services.worker.app.tasks.job_executor import execute_job, deliver_job_results, cleanup_job_resources
print('✓ Celery tasks imported successfully')
"
```

### 2. Test Job Execution (End-to-End)

```bash
# Ensure Celery worker is running
celery -A services.worker.app.celery_app worker -l info &

# Create and start a job via API
TOKEN="your-auth-token"
API_URL="http://localhost:8000"

JOB=$(curl -s -X POST "$API_URL/api/jobs/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"spec": "# Job: E2E Test\n## Tasks\n1. **Simple Task**\n   - Just a test"}')
JOB_ID=$(echo $JOB | jq -r '.id')

# Start the job
curl -s -X POST "$API_URL/api/jobs/$JOB_ID/start" -H "Authorization: Bearer $TOKEN"

# Poll for completion (max 60s)
for i in {1..12}; do
  STATUS=$(curl -s "$API_URL/api/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN" | jq -r '.status')
  echo "Status: $STATUS"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 5
done

# Check results
curl -s "$API_URL/api/jobs/$JOB_ID/results" -H "Authorization: Bearer $TOKEN" | jq .
```

### 3. Check Celery Logs

```bash
# Look for job execution logs
docker-compose logs worker | grep "job_executor"
```

### Expected Outcomes

- [ ] Celery tasks importable
- [ ] Job transitions: pending → queued → executing → completed/failed
- [ ] Tasks execute in sequence
- [ ] Results aggregated in job.results
- [ ] Ephemeral agents created if no match
- [ ] Delivery tasks triggered on completion
