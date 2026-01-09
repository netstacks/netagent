"""Approval request tool for agents to request human approval.

Allows agents to pause execution and request human approval
for risky or high-impact actions.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable

from ..llm.agent_executor import ToolDefinition, AgentEvent

logger = logging.getLogger(__name__)


class RequestApprovalTool:
    """Tool for requesting human approval before proceeding.

    Used by agents when they identify risky actions that
    require human oversight before execution.
    """

    name = "request_approval"
    description = """Request human approval before proceeding with a risky or high-impact action.

Use this tool when:
- About to make configuration changes to network devices
- About to execute commands that could affect production
- When the action has potential for significant impact
- When you need confirmation before proceeding

The request will be sent to the appropriate approvers and the conversation
will pause until approval is granted or denied.
"""
    requires_approval = False  # This tool itself doesn't need approval
    risk_level = "low"

    def __init__(
        self,
        db_session_factory: Callable,
        session_id: int,
        event_callback: Optional[Callable] = None,
        default_timeout_minutes: int = 60,
    ):
        """Initialize the approval tool.

        Args:
            db_session_factory: Factory for database sessions
            session_id: Current agent session ID
            event_callback: Optional async callback for SSE events
            default_timeout_minutes: Default approval timeout in minutes
        """
        self.db_session_factory = db_session_factory
        self.session_id = session_id
        self.event_callback = event_callback
        self.default_timeout_minutes = default_timeout_minutes

    @property
    def parameters(self) -> Dict[str, Any]:
        """Return OpenAI-style parameters schema."""
        return {
            "type": "object",
            "properties": {
                "action_description": {
                    "type": "string",
                    "description": "Clear description of the action that needs approval",
                },
                "action_type": {
                    "type": "string",
                    "description": "Type of action (e.g., 'config_change', 'command_execution', 'data_modification')",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Assessed risk level of the action",
                },
                "details": {
                    "type": "object",
                    "description": "Additional details about the action (device names, commands, etc.)",
                },
                "timeout_minutes": {
                    "type": "integer",
                    "description": "How long to wait for approval (default: 60 minutes)",
                },
            },
            "required": ["action_description", "action_type", "risk_level"],
        }

    async def execute(
        self,
        action_description: str,
        action_type: str,
        risk_level: str,
        details: Optional[Dict[str, Any]] = None,
        timeout_minutes: Optional[int] = None,
    ) -> str:
        """Request approval for an action.

        Args:
            action_description: Clear description of the action
            action_type: Type of action
            risk_level: Assessed risk level
            details: Additional details
            timeout_minutes: Timeout for approval

        Returns:
            String message describing the approval request status
        """
        from netagent_core.db import Approval, AgentSession

        timeout = timeout_minutes or self.default_timeout_minutes
        expires_at = datetime.utcnow() + timedelta(minutes=timeout)

        try:
            with self.db_session_factory() as db:
                # Create approval request
                approval = Approval(
                    session_id=self.session_id,
                    action_type=action_type,
                    action_description=action_description,
                    action_details=details or {},
                    risk_level=risk_level,
                    status="pending",
                    expires_at=expires_at,
                )
                db.add(approval)

                # Update session status to waiting
                session = db.query(AgentSession).filter(
                    AgentSession.id == self.session_id
                ).first()
                if session:
                    session.status = "waiting_approval"

                db.commit()
                db.refresh(approval)
                approval_id = approval.id

                logger.info(
                    f"Approval requested: id={approval_id}, type={action_type}, "
                    f"risk={risk_level}, session={self.session_id}"
                )

                # Emit SSE event if callback provided
                if self.event_callback:
                    await self.event_callback(AgentEvent(
                        event_type="approval_requested",
                        data={
                            "approval_id": approval_id,
                            "action_type": action_type,
                            "action_description": action_description,
                            "risk_level": risk_level,
                            "expires_at": expires_at.isoformat(),
                        }
                    ))

                return (
                    f"Approval request created successfully.\n"
                    f"- Approval ID: {approval_id}\n"
                    f"- Status: pending\n"
                    f"- Action: {action_description}\n"
                    f"- Risk Level: {risk_level}\n"
                    f"- Expires: {expires_at.isoformat()}\n\n"
                    f"Waiting for human approval. The request will expire in {timeout} minutes. "
                    f"The conversation will pause until approval is granted or denied."
                )

        except Exception as e:
            logger.error(f"Failed to create approval request: {e}")
            return f"Failed to create approval request: {str(e)}"

    def get_tool_definition(self) -> ToolDefinition:
        """Return tool definition for the agent executor."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            handler=self.execute,
            requires_approval=self.requires_approval,
            risk_level=self.risk_level,
        )


def create_approval_tool(
    db_session_factory: Callable,
    session_id: int,
    event_callback: Optional[Callable] = None,
) -> ToolDefinition:
    """Create an approval tool instance.

    Args:
        db_session_factory: Factory for database sessions
        session_id: Current agent session ID
        event_callback: Optional async callback for events

    Returns:
        ToolDefinition for the approval tool
    """
    tool = RequestApprovalTool(
        db_session_factory=db_session_factory,
        session_id=session_id,
        event_callback=event_callback,
    )
    return tool.get_tool_definition()
