"""Celery task for AI-powered alert triage.

When an alert is ingested, this task:
1. Loads the alert from DB
2. Finds the designated Triage Agent
3. Builds a prompt with alert context
4. Creates an AgentSession and runs the Triage Agent
5. The Triage Agent handles routing, investigation, and resolution
"""

import logging
from datetime import datetime
from celery import shared_task

from netagent_core.db import get_db_context, Alert, Agent, AgentSession
from netagent_core.redis_events import publish_alert_event

logger = logging.getLogger(__name__)

# The Triage Agent is found by agent_type
TRIAGE_AGENT_TYPE = "alert_triage"


def _build_triage_prompt(alert: Alert) -> str:
    """Build the initial prompt for the Triage Agent with alert details."""
    lines = [
        f"## Incoming Network Alert (ID: {alert.id})",
        "",
        f"**Severity:** {alert.severity.upper()}",
        f"**Type:** {alert.alert_type or 'Unknown'}",
        f"**Source:** {alert.source_type}",
        f"**Title:** {alert.title}",
    ]

    if alert.device_name:
        lines.append(f"**Device:** {alert.device_name}")
    if alert.device_ip:
        lines.append(f"**Device IP:** {alert.device_ip}")
    if alert.interface_name:
        lines.append(f"**Interface:** {alert.interface_name}")
    if alert.correlation_count > 1:
        lines.append(f"**Occurrences:** {alert.correlation_count} (within dedup window)")

    if alert.description:
        lines.extend(["", "### Alert Description", alert.description[:2000]])

    lines.extend([
        "",
        "### Instructions",
        "Triage this alert following your standard process. "
        "Check for related alerts, search knowledge bases for runbooks, "
        "and either hand off to a specialist agent or investigate directly.",
    ])

    return "\n".join(lines)


@shared_task(bind=True, max_retries=2)
def triage_alert(self, alert_id: int):
    """Triage an incoming network alert using the AI Triage Agent.

    Args:
        alert_id: ID of the Alert to triage
    """
    logger.info(f"Starting triage for alert {alert_id}")

    with get_db_context() as db:
        # Load alert
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        if not alert:
            logger.error(f"Alert {alert_id} not found")
            return {"error": "Alert not found"}

        if alert.status not in ("new",):
            logger.info(f"Alert {alert_id} already in status '{alert.status}', skipping triage")
            return {"skipped": True, "status": alert.status}

        # Find the Triage Agent
        triage_agent = db.query(Agent).filter(
            Agent.agent_type == TRIAGE_AGENT_TYPE,
            Agent.enabled == True,
            Agent.is_ephemeral == False,
        ).first()

        if not triage_agent:
            logger.warning("No triage agent configured, looking for any triage-type agent")
            triage_agent = db.query(Agent).filter(
                Agent.agent_type == "triage",
                Agent.enabled == True,
                Agent.is_ephemeral == False,
            ).first()

        if not triage_agent:
            logger.error("No triage agent found! Cannot process alert.")
            alert.status = "new"  # Keep as new for manual handling
            db.commit()
            return {"error": "No triage agent configured"}

        # Build the triage prompt
        prompt = _build_triage_prompt(alert)

        # Create agent session
        session = AgentSession(
            agent_id=triage_agent.id,
            status="pending",
            trigger_type="alert",
            context={
                "alert_id": alert.id,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "device_name": alert.device_name,
            },
        )
        db.add(session)

        # Update alert status
        alert.status = "triaging"
        alert.triage_session_id = None  # Will be set after commit

        db.commit()
        db.refresh(session)

        # Now set the session ID on the alert
        alert.triage_session_id = session.id
        db.commit()

        session_id = session.id
        logger.info(f"Created triage session {session_id} for alert {alert_id} using agent '{triage_agent.name}'")

    # Publish event for live dashboard
    publish_alert_event("alert_triaging", {
        "alert_id": alert_id,
        "session_id": session_id,
        "agent_name": triage_agent.name,
    })

    # Execute the agent session via the existing task
    from tasks.agent_executor import execute_agent_session
    try:
        result = execute_agent_session(session_id, prompt)
        logger.info(f"Triage completed for alert {alert_id}: {result}")
        return result
    except Exception as e:
        logger.exception(f"Triage failed for alert {alert_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30)
        return {"error": str(e)}
