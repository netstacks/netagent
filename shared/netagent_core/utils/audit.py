"""Audit logging utilities."""

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Dict, TYPE_CHECKING

from sqlalchemy.orm import Session

from ..db.models import AuditLog

# Import Request and ALBUser only for type checking to avoid requiring fastapi in worker
if TYPE_CHECKING:
    from fastapi import Request
    from ..auth.alb_auth import ALBUser

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Audit event types."""

    # Agent events
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_DELETED = "agent.deleted"
    AGENT_CHAT_STARTED = "agent.chat.started"
    AGENT_CHAT_COMPLETED = "agent.chat.completed"

    # Workflow events
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_UPDATED = "workflow.updated"
    WORKFLOW_DELETED = "workflow.deleted"
    WORKFLOW_RUN_STARTED = "workflow.run.started"
    WORKFLOW_RUN_COMPLETED = "workflow.run.completed"
    WORKFLOW_RUN_FAILED = "workflow.run.failed"

    # Knowledge events
    KNOWLEDGE_CREATED = "knowledge.created"
    KNOWLEDGE_UPDATED = "knowledge.updated"
    KNOWLEDGE_DELETED = "knowledge.deleted"
    KNOWLEDGE_SYNC_STARTED = "knowledge.sync.started"
    KNOWLEDGE_SYNC_COMPLETED = "knowledge.sync.completed"

    # Device credential events
    DEVICE_CREDENTIAL_CREATED = "device.credential.created"
    DEVICE_CREDENTIAL_UPDATED = "device.credential.updated"
    DEVICE_CREDENTIAL_DELETED = "device.credential.deleted"

    # MCP events
    MCP_SERVER_CREATED = "mcp.server.created"
    MCP_SERVER_UPDATED = "mcp.server.updated"
    MCP_SERVER_DELETED = "mcp.server.deleted"
    MCP_TOOL_CALLED = "mcp.tool.called"

    # API Resource events
    API_RESOURCE_CREATED = "api_resource.created"
    API_RESOURCE_UPDATED = "api_resource.updated"
    API_RESOURCE_DELETED = "api_resource.deleted"
    API_RESOURCE_CALLED = "api_resource.called"

    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXPIRED = "approval.expired"

    # Security events
    USER_LOGIN = "security.login"
    USER_LOGOUT = "security.logout"
    UNAUTHORIZED_ACCESS = "security.unauthorized"

    # System events
    SETTINGS_UPDATED = "system.settings.updated"

    # Job events
    JOB_CREATED = "job.created"
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"


def audit_log(
    db: Session,
    event_type: AuditEventType,
    user: Optional["ALBUser"] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    resource_name: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Optional["Request"] = None,
) -> AuditLog:
    """Log an audit event.

    Args:
        db: Database session
        event_type: Type of event
        user: User who performed the action (if any)
        resource_type: Type of resource affected (e.g., "agent", "workflow")
        resource_id: ID of the resource
        resource_name: Name of the resource (for display)
        action: Action performed (e.g., "create", "update", "delete")
        details: Additional details as dictionary
        request: FastAPI request object (for IP, user agent)

    Returns:
        Created AuditLog entry
    """
    # Extract category from event type
    category = event_type.value.split(".")[0]

    # Get request context
    ip_address = None
    user_agent = None
    if request:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    entry = AuditLog(
        event_type=event_type.value,
        event_category=category,
        user_id=user.id if user else None,
        user_email=user.email if user else None,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=datetime.utcnow(),
    )

    db.add(entry)
    db.commit()

    logger.info(
        f"Audit: {event_type.value} by {user.email if user else 'system'} "
        f"on {resource_type}:{resource_id}"
    )

    return entry


def audit_log_async(
    db: Session,
    event_type: AuditEventType,
    **kwargs,
) -> None:
    """Log audit event without blocking (commits immediately).

    Use this for non-critical audit events where you don't need the entry returned.
    """
    try:
        audit_log(db, event_type, **kwargs)
    except Exception as e:
        logger.error(f"Failed to log audit event {event_type}: {e}")
