"""Trigger routes for agent webhooks."""

import hmac
import hashlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Agent, AgentSession
from netagent_core.utils import audit_log, AuditEventType

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/agent/{agent_id}")
async def webhook_trigger_agent(
    agent_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_signature: Optional[str] = Header(None),
):
    """Webhook endpoint to trigger an agent session.

    The webhook payload becomes the initial context for the agent.
    Optionally validates signature using agent's webhook_secret.
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")

    # Get request body
    body = await request.body()

    # Validate signature if webhook_secret is set on agent
    webhook_secret = getattr(agent, 'webhook_secret', None)
    if webhook_secret:
        if not x_webhook_signature:
            raise HTTPException(status_code=401, detail="Missing webhook signature")

        expected_signature = hmac.new(
            webhook_secret.encode(),
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
    try:
        trigger_data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        trigger_data = {"raw_body": body.decode("utf-8", errors="replace")}

    # Extract message from trigger data, or use the whole payload as context
    initial_message = trigger_data.get("message", trigger_data.get("prompt", None))
    if not initial_message:
        # If no message field, summarize the payload
        initial_message = f"Webhook triggered with payload: {json.dumps(trigger_data)}"

    # Create agent session
    session = AgentSession(
        agent_id=agent_id,
        status="pending",
        trigger_type="webhook",
        context=trigger_data,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    # Queue async agent execution via Celery
    try:
        from celery import current_app
        current_app.send_task(
            'tasks.agent_executor.execute_agent_session',
            args=[session.id, initial_message],
        )
        logger.info(f"Queued agent execution for session {session.id}")
    except Exception as e:
        logger.warning(f"Failed to queue agent execution (Celery may not be available): {e}")
        # Continue anyway - client can trigger via SSE endpoint

    audit_log(
        db,
        AuditEventType.AGENT_CHAT_STARTED,
        resource_type="agent_session",
        resource_id=session.id,
        resource_name=agent.name,
        action="webhook_trigger",
        details={
            "source_ip": request.client.host if request.client else None,
            "trigger_data_keys": list(trigger_data.keys()) if isinstance(trigger_data, dict) else None,
        },
    )

    return {
        "session_id": session.id,
        "agent_id": agent_id,
        "agent_name": agent.name,
        "status": "pending",
        "message": "Agent session created",
    }
