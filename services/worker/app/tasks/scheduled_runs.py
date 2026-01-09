"""Scheduled tasks runner."""

import logging
from datetime import datetime, timedelta
from celery import shared_task

from netagent_core.db import get_db_context, Agent, AgentSession, Approval, ScheduledTask

logger = logging.getLogger(__name__)


@shared_task
def check_scheduled_tasks():
    """Check for scheduled tasks that need to run.

    This task runs every minute and checks cron expressions.
    """
    with get_db_context() as db:
        # Get enabled scheduled tasks with enabled agents
        tasks = db.query(ScheduledTask).join(Agent).filter(
            ScheduledTask.enabled == True,
            Agent.enabled == True,
        ).all()

        triggered = 0
        for task in tasks:
            if should_run_now(task.schedule_cron, task.last_run_at):
                # Create agent session
                session = AgentSession(
                    agent_id=task.agent_id,
                    status="pending",
                    trigger_type="scheduled",
                    context={
                        "scheduled_task_id": task.id,
                        "scheduled_task_name": task.name,
                        "scheduled_at": datetime.utcnow().isoformat(),
                        "cron": task.schedule_cron,
                    },
                )
                db.add(session)
                db.flush()  # Get session.id

                # Update task status
                task.last_run_at = datetime.utcnow()
                task.last_run_status = "running"
                task.last_session_id = session.id
                db.commit()

                # Queue execution
                from tasks.agent_executor import execute_agent_session
                execute_agent_session.delay(session.id, task.prompt)

                triggered += 1
                logger.info(f"Triggered scheduled task: {task.name} -> agent {task.agent.name} (session {session.id})")

        return {"checked": len(tasks), "triggered": triggered}


# Keep old function name as alias for backwards compatibility
check_scheduled_agents = check_scheduled_tasks


def should_run_now(cron_expression: str, last_run: datetime = None) -> bool:
    """Check if a cron expression should run now.

    Args:
        cron_expression: Cron expression (e.g., "0 8 * * *")
        last_run: Last time this schedule was triggered

    Returns:
        True if should run now
    """
    try:
        from croniter import croniter

        now = datetime.utcnow()
        cron = croniter(cron_expression, now)
        prev_run = cron.get_prev(datetime)

        # Check if previous scheduled time was within the last minute
        diff = (now - prev_run).total_seconds()
        if diff >= 60:
            return False

        # Prevent duplicate runs - check if we already ran for this schedule
        if last_run:
            # If last run was within 60 seconds of prev_run, skip
            last_run_diff = abs((last_run - prev_run).total_seconds())
            if last_run_diff < 60:
                return False

        return True

    except ImportError:
        # Fallback: simple minute-based check without croniter
        logger.warning("croniter not installed, using simple schedule check")
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

        # Simple duplicate prevention
        if last_run and (now - last_run).total_seconds() < 60:
            return False

        return True

    except Exception as e:
        logger.error(f"Error checking schedule: {e}")
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
