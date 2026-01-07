"""Scheduled workflow tasks."""

import logging
from datetime import datetime
from celery import shared_task

from netagent_core.db import get_db_context, Workflow, WorkflowRun, Approval

logger = logging.getLogger(__name__)


@shared_task
def check_scheduled_workflows():
    """Check for workflows that need to run on schedule.

    This task runs every minute and checks cron expressions.
    """
    with get_db_context() as db:
        # Get scheduled workflows
        workflows = db.query(Workflow).filter(
            Workflow.trigger_type == "scheduled",
            Workflow.enabled == True,
            Workflow.schedule_cron.isnot(None),
        ).all()

        triggered = 0
        for workflow in workflows:
            if should_run_now(workflow.schedule_cron):
                # Create workflow run
                run = WorkflowRun(
                    workflow_id=workflow.id,
                    status="pending",
                    trigger_type="scheduled",
                    trigger_data={"scheduled_at": datetime.utcnow().isoformat()},
                    context={},
                )
                db.add(run)
                db.commit()

                # Queue execution
                from tasks.workflow_executor import execute_workflow
                execute_workflow.delay(run.id)

                triggered += 1
                logger.info(f"Triggered scheduled workflow: {workflow.name}")

        return {"checked": len(workflows), "triggered": triggered}


def should_run_now(cron_expression: str) -> bool:
    """Check if a cron expression should run now.

    Simple cron matching - for production, use croniter library.
    """
    try:
        from croniter import croniter
        cron = croniter(cron_expression, datetime.utcnow())
        prev_run = cron.get_prev(datetime)
        now = datetime.utcnow()

        # Check if previous scheduled time was within the last minute
        diff = (now - prev_run).total_seconds()
        return diff < 60
    except ImportError:
        # Fallback: simple minute-based check
        parts = cron_expression.split()
        if len(parts) != 5:
            return False

        minute, hour, day, month, weekday = parts
        now = datetime.utcnow()

        # Check minute
        if minute != "*" and int(minute) != now.minute:
            return False
        # Check hour
        if hour != "*" and int(hour) != now.hour:
            return False

        return True
    except Exception:
        return False


@shared_task
def cleanup_expired_approvals():
    """Mark expired approvals as expired.

    This task runs every 5 minutes.
    """
    with get_db_context() as db:
        now = datetime.utcnow()

        expired = db.query(Approval).filter(
            Approval.status == "pending",
            Approval.expires_at < now,
        ).all()

        for approval in expired:
            approval.status = "expired"
            logger.info(f"Approval expired: {approval.id}")

        if expired:
            db.commit()

        return {"expired": len(expired)}


@shared_task
def resume_waiting_workflows():
    """Resume workflows that were waiting for approval.

    Called after an approval is granted.
    """
    with get_db_context() as db:
        # Find workflow runs waiting for approval that have been approved
        from sqlalchemy import and_

        waiting_runs = db.query(WorkflowRun).filter(
            WorkflowRun.status == "waiting_approval"
        ).all()

        resumed = 0
        for run in waiting_runs:
            # Check if all pending approvals for this run are resolved
            pending = db.query(Approval).filter(
                Approval.workflow_run_id == run.id,
                Approval.status == "pending",
            ).count()

            if pending == 0:
                # Check if any were rejected
                rejected = db.query(Approval).filter(
                    Approval.workflow_run_id == run.id,
                    Approval.status == "rejected",
                ).count()

                if rejected > 0:
                    run.status = "failed"
                    run.error_message = "Approval rejected"
                else:
                    # Resume workflow
                    run.status = "running"
                    from tasks.workflow_executor import execute_workflow
                    execute_workflow.delay(run.id)
                    resumed += 1

        if waiting_runs:
            db.commit()

        return {"checked": len(waiting_runs), "resumed": resumed}
