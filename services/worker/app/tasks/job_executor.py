"""Celery tasks for job orchestration.

Handles execution of multi-step jobs by:
1. Building dependency graph from tasks
2. Matching tasks to agents (or creating ephemeral ones)
3. Executing tasks in dependency order, passing results between them
4. Handling failures according to job configuration
5. Delivering results to configured channels
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from celery import shared_task

from netagent_core.db import (
    get_db_context,
    Job,
    JobTask,
    Agent,
    AgentSession,
    AgentMessage,
)
from netagent_core.job import AgentMatcher, JobOrchestrator
from netagent_core.redis_events import check_cancel_flag, check_job_cancel_flag, publish_session_event

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when a job is cancelled."""
    pass


def check_job_cancelled(job_id: int) -> bool:
    """Check if a job has been cancelled via Redis flag or database status."""
    # Check Redis first (faster)
    if check_job_cancel_flag(job_id):
        return True
    # Fall back to database check
    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()
        return job.status == "cancelled" if job else True


@shared_task(bind=True, max_retries=2)
def execute_job(self, job_id: int):
    """Execute a job orchestration task.

    This uses the JobOrchestrator to execute tasks in proper dependency order,
    passing results from completed tasks to dependent tasks.

    Args:
        job_id: ID of the Job to execute
    """
    logger.info(f"Starting orchestrated job execution: job_id={job_id}")

    try:
        # Run the async orchestration in an event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_orchestrate_job(job_id))
        finally:
            loop.close()

        return result

    except JobCancelled:
        logger.info(f"Job {job_id} cancelled during execution")
        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "cancelled"
                job.completed_at = datetime.utcnow()
                db.commit()
        cleanup_job_resources.delay(job_id)
        return {"cancelled": True, "job_id": job_id}

    except Exception as e:
        logger.exception(f"Job {job_id} execution failed: {e}")
        with get_db_context() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_summary = str(e)
                job.completed_at = datetime.utcnow()
                db.commit()

        if self.request.retries >= self.max_retries:
            cleanup_job_resources.delay(job_id)
            return {"error": str(e), "job_id": job_id}

        raise self.retry(exc=e, countdown=60)


async def _orchestrate_job(job_id: int) -> Dict[str, Any]:
    """Orchestrate job execution with proper dependency handling.

    This function:
    1. Initializes the orchestrator and builds the dependency graph
    2. Iteratively executes ready tasks (those with satisfied dependencies)
    3. Passes outputs from completed tasks to dependent tasks
    4. Handles failures according to job configuration

    Args:
        job_id: ID of the job to orchestrate

    Returns:
        Execution result dict
    """
    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        if job.status == "cancelled":
            logger.info(f"Job {job_id} was cancelled before execution")
            return {"cancelled": True}

        # Update status to executing
        job.status = "executing"
        job.started_at = datetime.utcnow()
        db.commit()

        # Initialize orchestrator
        orchestrator = JobOrchestrator(job_id, db)
        if not orchestrator.initialize():
            job.status = "failed"
            job.error_summary = "Failed to initialize orchestrator"
            job.completed_at = datetime.utcnow()
            db.commit()
            return {"error": "Failed to initialize orchestrator"}

        logger.info(f"Job {job_id}: Orchestrating {len(orchestrator.graph.nodes)} tasks")

        results = {}
        execution_mode = job.execution_mode

        # Main orchestration loop - keep executing until all tasks are done
        while not orchestrator.is_complete():
            # Check for cancellation
            if check_job_cancelled(job_id):
                raise JobCancelled()

            # Get tasks that are ready to execute
            ready_tasks = orchestrator.get_next_tasks()

            if not ready_tasks:
                # No ready tasks but not complete - might be stuck
                status = orchestrator.get_status()
                if status["running"] == 0 and status["pending"] > 0:
                    logger.error(f"Job {job_id}: Deadlock detected - pending tasks with no ready tasks")
                    break
                # Tasks still running, wait a bit
                await asyncio.sleep(1)
                continue

            # Execute ready tasks based on mode
            if execution_mode == "sequential":
                # Execute one at a time
                task = ready_tasks[0]
                await _execute_orchestrated_task(db, orchestrator, job, task)
            elif execution_mode == "parallel":
                # Execute all ready tasks in parallel
                await asyncio.gather(*[
                    _execute_orchestrated_task(db, orchestrator, job, task)
                    for task in ready_tasks
                ])
            else:  # batch
                # Execute up to batch_size at a time
                batch_size = job.batch_size or 5
                batch = ready_tasks[:batch_size]
                await asyncio.gather(*[
                    _execute_orchestrated_task(db, orchestrator, job, task)
                    for task in batch
                ])

        # Collect final results
        for seq, node in orchestrator.graph.nodes.items():
            results[f"task_{seq}"] = {
                "name": node.name,
                "status": node.status,
                "success": node.status == "completed",
                "output": node.result,
                "error": node.error,
            }

        # Update job with results
        status = orchestrator.get_status()
        job.results = results
        job.completed_tasks = status["completed"]
        job.failed_tasks = status["failed"]
        job.completed_at = datetime.utcnow()

        if status["failed"] > 0 and job.on_failure == "stop":
            job.status = "failed"
            job.error_summary = f"{status['failed']} task(s) failed, {status['skipped']} skipped"
        elif status["failed"] > 0:
            job.status = "completed"  # Completed with errors
            job.error_summary = f"{status['failed']} task(s) failed"
        else:
            job.status = "completed"

        db.commit()

        logger.info(f"Job {job_id} completed: status={job.status}, {status}")

        # Trigger delivery if configured
        if job.delivery_config and job.status == "completed":
            deliver_job_results.delay(job_id)

        # Clean up ephemeral agents
        cleanup_job_resources.delay(job_id)

        return {
            "success": job.status == "completed" and status["failed"] == 0,
            "job_id": job_id,
            "status": job.status,
            "completed_tasks": status["completed"],
            "failed_tasks": status["failed"],
            "skipped_tasks": status["skipped"],
        }


async def _execute_orchestrated_task(
    db,
    orchestrator: JobOrchestrator,
    job: Job,
    task_node,
) -> Dict[str, Any]:
    """Execute a single task within the orchestrated flow.

    This function:
    1. Builds context from dependency outputs
    2. Finds or creates an agent
    3. Executes the agent with the enhanced prompt
    4. Reports results back to orchestrator

    Args:
        db: Database session
        orchestrator: The job orchestrator
        job: Parent job
        task_node: TaskNode from the dependency graph

    Returns:
        Execution result dict
    """
    task_seq = task_node.sequence

    # Get the actual JobTask from DB
    db_task = db.query(JobTask).filter(
        JobTask.job_id == job.id,
        JobTask.sequence == task_seq
    ).first()

    if not db_task:
        orchestrator.fail_task(task_seq, "Task not found in database")
        return {"success": False, "error": "Task not found"}

    logger.info(f"Job {job.id}: Executing task {task_seq}: {task_node.name}")

    try:
        # Update task status
        db_task.status = "running"
        db_task.started_at = datetime.utcnow()
        db.commit()

        # Build context with dependency outputs
        context = orchestrator.build_task_context(task_node)

        # Find or create agent
        matcher = AgentMatcher(db)
        agent, score, reason = matcher.find_best_agent(
            task_name=task_node.name,
            task_description=task_node.description,
            agent_hint=task_node.agent_hint,
        )

        if agent:
            logger.info(f"Task {task_seq} matched agent '{agent.name}' (score: {score:.2f}, reason: {reason})")
            db_task.agent_id = agent.id
        else:
            # Create ephemeral agent with inferred tools
            logger.info(f"Task {task_seq} creating ephemeral agent")

            tools = list(matcher._infer_required_tools(
                f"{task_node.name} {task_node.description}".lower()
            ))
            if not tools:
                tools = ["ssh_command"]  # Default fallback

            agent = matcher.create_ephemeral_agent(
                task_name=task_node.name,
                task_description=task_node.description,
                job_id=job.id,
                tools=tools,
                job_context=context,
            )
            db_task.is_ephemeral_agent = True
            db_task.ephemeral_agent_id = agent.id
            db_task.ephemeral_prompt = agent.system_prompt

        db.commit()

        # Create agent session
        session = AgentSession(
            agent_id=agent.id,
            user_id=job.created_by,
            status="active",
            trigger_type="job",
            context={"job_id": job.id, "task_id": db_task.id, "task_sequence": task_seq},
        )
        db.add(session)
        db.commit()

        db_task.session_id = session.id
        db.commit()

        # Build the enhanced prompt with dependency outputs
        prompt = orchestrator.build_task_prompt(task_node, context)

        # Execute the agent session
        from tasks.agent_executor import _run_agent_session
        result = await _run_agent_session(session.id, prompt)

        # Collect tool results if available
        tool_results = []
        if result.get("success"):
            # Query tool results from AgentAction
            from netagent_core.db import AgentAction
            actions = db.query(AgentAction).filter(
                AgentAction.session_id == session.id,
                AgentAction.action_type == "tool_call",
                AgentAction.status == "completed",
            ).all()

            for action in actions:
                if action.tool_output:
                    tool_results.append({
                        "name": action.tool_name,
                        "result": action.tool_output.get("result") if isinstance(action.tool_output, dict) else action.tool_output,
                    })

        # Report result to orchestrator
        if result.get("success"):
            orchestrator.complete_task(task_seq, result, tool_results)
            job.completed_tasks += 1
        elif result.get("cancelled"):
            db_task.status = "skipped"
            db_task.error = "Job cancelled"
        else:
            error = result.get("error") or result.get("reason") or "Unknown error"
            skipped = orchestrator.fail_task(task_seq, error)
            job.failed_tasks += 1

            if skipped:
                logger.info(f"Task {task_seq} failure caused {len(skipped)} tasks to be skipped")

        db.commit()

        return {
            "success": db_task.status == "completed",
            "output": db_task.result,
            "error": db_task.error,
            "session_id": session.id,
        }

    except Exception as e:
        logger.exception(f"Task {task_seq} execution failed: {e}")
        orchestrator.fail_task(task_seq, str(e))
        job.failed_tasks += 1
        db.commit()

        return {
            "success": False,
            "error": str(e),
        }


@shared_task
def deliver_job_results(job_id: int):
    """Deliver job results to configured channels.

    Supports:
    - email: Send results via email
    - slack: Post to Slack channel
    - webhook: POST to webhook URL
    - s3: Upload to S3 bucket
    """
    logger.info(f"Delivering results for job {job_id}")

    with get_db_context() as db:
        job = db.query(Job).filter(Job.id == job_id).first()

        if not job:
            logger.error(f"Job {job_id} not found for delivery")
            return

        if not job.delivery_config:
            logger.info(f"Job {job_id} has no delivery configuration")
            return

        config = job.delivery_config
        results_summary = _format_results_summary(job)

        # Email delivery
        for email in config.get("email", []):
            try:
                from tasks.notifications import send_email
                send_email.delay(
                    to=[email] if isinstance(email, str) else email,
                    subject=f"Job Results: {job.name}",
                    body=results_summary,
                )
                logger.info(f"Job {job_id} results sent to {email}")
            except Exception as e:
                logger.error(f"Email delivery failed for job {job_id}: {e}")

        # Slack delivery
        for channel in config.get("slack", []):
            try:
                from tasks.notifications import send_slack_message
                send_slack_message.delay(
                    channel=channel,
                    text=f"*Job Completed: {job.name}*\n{results_summary[:3000]}",
                )
                logger.info(f"Job {job_id} results posted to Slack {channel}")
            except Exception as e:
                logger.error(f"Slack delivery failed for job {job_id}: {e}")

        # Webhook delivery
        for url in config.get("webhook", []):
            try:
                import httpx
                with httpx.Client() as client:
                    client.post(url, json={
                        "job_id": job_id,
                        "job_name": job.name,
                        "status": job.status,
                        "results": job.results,
                        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    }, timeout=30)
                logger.info(f"Job {job_id} results posted to webhook {url}")
            except Exception as e:
                logger.error(f"Webhook delivery failed for job {job_id}: {e}")


def _format_results_summary(job: Job) -> str:
    """Format job results as a human-readable summary."""
    lines = [
        f"Job: {job.name}",
        f"Status: {job.status}",
        f"Tasks: {job.completed_tasks}/{job.total_tasks} completed",
        "",
    ]

    if job.failed_tasks > 0:
        lines.append(f"Failed tasks: {job.failed_tasks}")

    if job.error_summary:
        lines.append(f"Errors: {job.error_summary}")

    lines.append("")

    if job.results:
        lines.append("Results:")
        for task_key, result in job.results.items():
            task_name = result.get("name", task_key)
            status = result.get("status", "unknown")
            lines.append(f"  {task_key} ({task_name}): {status}")

            if result.get("output"):
                output = result["output"]
                if isinstance(output, dict):
                    response = output.get("response", "")[:300]
                else:
                    response = str(output)[:300]
                if response:
                    lines.append(f"    Response: {response}...")

            if result.get("error"):
                lines.append(f"    Error: {result['error']}")

    return "\n".join(lines)


@shared_task
def cleanup_job_resources(job_id: int):
    """Clean up resources after job completion.

    - Delete ephemeral agents created for this job
    - Delete associated agent sessions
    """
    logger.info(f"Cleaning up resources for job {job_id}")

    with get_db_context() as db:
        # Find ephemeral agent IDs for this job
        ephemeral_agent_ids = [
            agent_id for (agent_id,) in db.query(Agent.id).filter(
                Agent.is_ephemeral == True,
                Agent.created_for_job_id == job_id,
            ).all()
        ]

        if ephemeral_agent_ids:
            # Delete messages for sessions of these agents first
            session_ids = [
                session_id for (session_id,) in db.query(AgentSession.id).filter(
                    AgentSession.agent_id.in_(ephemeral_agent_ids)
                ).all()
            ]

            if session_ids:
                deleted_messages = db.query(AgentMessage).filter(
                    AgentMessage.session_id.in_(session_ids)
                ).delete(synchronize_session=False)

            # Delete sessions for these agents
            deleted_sessions = db.query(AgentSession).filter(
                AgentSession.agent_id.in_(ephemeral_agent_ids)
            ).delete(synchronize_session=False)

            # Now delete the ephemeral agents
            deleted_agents = db.query(Agent).filter(
                Agent.id.in_(ephemeral_agent_ids)
            ).delete(synchronize_session=False)

            db.commit()

            logger.info(
                f"Cleaned up job {job_id}: deleted {deleted_agents} ephemeral agents "
                f"and {deleted_sessions} sessions"
            )
        else:
            logger.info(f"No ephemeral agents to clean up for job {job_id}")
