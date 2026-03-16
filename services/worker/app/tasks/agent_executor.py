"""Celery tasks for async agent execution.

Handles background agent execution triggered by webhooks or scheduled runs.
"""

import asyncio
import logging
import time
from datetime import datetime
from celery import shared_task

from netagent_core.db import (
    get_db_context,
    Agent,
    AgentSession,
    AgentMessage,
    AgentAction,
)
from netagent_core.llm import GeminiClient, AgentExecutor
from netagent_core.llm.agent_executor import ToolDefinition
from netagent_core.redis_events import check_cancel_flag, publish_live_session_event

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Raised when a task is cancelled by the user."""
    pass


class ApprovalRejected(Exception):
    """Raised when an approval request is rejected."""
    pass


class ApprovalExpired(Exception):
    """Raised when an approval request expires."""
    pass


def wait_for_approval(session_id: int, approval_id: int, poll_interval: int = 5, timeout_seconds: int = 3600):
    """Block until an approval is resolved or timeout.

    Args:
        session_id: The session ID
        approval_id: The approval ID to wait for
        poll_interval: Seconds between database polls
        timeout_seconds: Maximum seconds to wait

    Returns:
        True if approved

    Raises:
        TaskCancelled: If session is cancelled
        ApprovalRejected: If approval is rejected
        ApprovalExpired: If approval expires
    """
    from netagent_core.db import Approval

    start_time = time.time()

    while True:
        # Check for cancellation
        if check_cancel_flag(session_id):
            raise TaskCancelled("Session cancelled by user")

        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise ApprovalExpired()

        # Poll approval status
        with get_db_context() as db:
            approval = db.query(Approval).filter(Approval.id == approval_id).first()

            if not approval:
                raise ApprovalRejected("Approval not found")

            if approval.status == "approved":
                logger.info(f"Approval {approval_id} granted, resuming session {session_id}")
                return True
            elif approval.status == "rejected":
                raise ApprovalRejected(approval.resolution_note or "Approval rejected")
            elif approval.status == "expired":
                raise ApprovalExpired()

        # Still pending - wait and poll again
        logger.debug(f"Approval {approval_id} still pending, waiting {poll_interval}s...")
        time.sleep(poll_interval)


def build_tools_for_agent(agent: Agent, db_session_factory, session_id: int):
    """Build non-async tools for agent execution.

    Note:
        Empty knowledge_base_ids means "use no knowledge bases".
        Resources must be explicitly configured per agent.
        MCP tools are loaded separately via build_mcp_tools_for_agent (async).
    """
    from netagent_core.tools import (
        create_ssh_tool, create_knowledge_search_tool, create_email_tool,
        create_nso_route_tool, create_nso_lldp_tool, create_nso_vrfs_tool,
        create_nso_arista_exec_tool, create_a10_cgnat_tool,
        create_eagleview_tool, create_netbox_search_tool,
    )
    import os

    tools = []
    allowed_tools = agent.allowed_tools or []
    encryption_key = os.getenv("ENCRYPTION_KEY")

    # SSH command tool
    if "ssh_command" in allowed_tools:
        tools.append(create_ssh_tool(
            allowed_device_patterns=agent.allowed_device_patterns or ["*"],
            db_session_factory=db_session_factory,
            encryption_key=encryption_key,
        ))

    # Knowledge search tool - only if knowledge bases are specified
    if "search_knowledge" in allowed_tools and agent.knowledge_base_ids:
        tools.append(create_knowledge_search_tool(
            knowledge_base_ids=agent.knowledge_base_ids,
            db_session_factory=db_session_factory,
        ))

    # Email tool
    if "send_email" in allowed_tools:
        tools.append(create_email_tool())

    # PathTrace tools
    if "nso_juniper_route" in allowed_tools:
        tools.append(create_nso_route_tool())
    if "nso_juniper_lldp" in allowed_tools:
        tools.append(create_nso_lldp_tool())
    if "nso_juniper_vrfs" in allowed_tools:
        tools.append(create_nso_vrfs_tool())
    if "nso_arista_exec" in allowed_tools:
        tools.append(create_nso_arista_exec_tool())
    if "a10_cgnat_lookup" in allowed_tools:
        tools.append(create_a10_cgnat_tool())
    if "eagleview_lookup" in allowed_tools:
        tools.append(create_eagleview_tool())
    if "netbox_search" in allowed_tools:
        tools.append(create_netbox_search_tool())

    return tools


async def build_mcp_tools_for_agent(agent: Agent, db_session_factory):
    """Build MCP tools for agent execution (async).

    Args:
        agent: Agent model with mcp_server_ids
        db_session_factory: Factory for database sessions

    Returns:
        List of ToolDefinitions for MCP tools
    """
    import os
    from netagent_core.tools.mcp_tool import load_mcp_tools_for_agent

    if not agent.mcp_server_ids:
        return []

    encryption_key = os.getenv("ENCRYPTION_KEY")

    try:
        mcp_tools = await load_mcp_tools_for_agent(
            mcp_server_ids=agent.mcp_server_ids,
            db_session_factory=db_session_factory,
            encryption_key=encryption_key,
        )
        logger.info(f"Loaded {len(mcp_tools)} MCP tools for agent {agent.id}")
        return mcp_tools
    except Exception as e:
        logger.error(f"Failed to load MCP tools for agent {agent.id}: {e}")
        return []


async def _run_agent_session(session_id: int, initial_message: str = None):
    """Async implementation of agent execution.

    Args:
        session_id: ID of the AgentSession to execute
        initial_message: Optional initial message to start the conversation

    Returns:
        Dict with execution results
    """
    logger.info(f"Starting agent execution: session_id={session_id}")

    with get_db_context() as db:
        # Get session and agent
        session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
        if not session:
            logger.error(f"Session not found: {session_id}")
            return {"error": "Session not found"}

        agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
        if not agent:
            logger.error(f"Agent not found: {session.agent_id}")
            session.status = "failed"
            db.commit()
            return {"error": "Agent not found"}

        if not agent.enabled:
            logger.error(f"Agent is disabled: {agent.id}")
            session.status = "failed"
            db.commit()
            return {"error": "Agent is disabled"}

        # Update session status
        session.status = "running"
        db.commit()

        try:
            # Build tools (sync tools first)
            tools = build_tools_for_agent(agent, get_db_context, session_id)

            # Load MCP tools (async)
            mcp_tools = await build_mcp_tools_for_agent(agent, get_db_context)
            tools.extend(mcp_tools)

            logger.info(f"Agent {agent.id} has {len(tools)} total tools available")

            # Create Gemini client
            client = GeminiClient(model=agent.model)

            # Create executor
            executor = AgentExecutor(
                client=client,
                system_prompt=agent.system_prompt,
                tools=tools,
                max_iterations=agent.max_iterations,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
            )

            # Determine initial message
            message = initial_message
            if not message:
                # Check session context for message
                context = session.context or {}
                message = context.get("message") or context.get("prompt")

            if not message:
                # Use default prompt based on trigger data
                trigger_data = session.context or {}
                message = f"Process this webhook trigger data: {trigger_data}"

            # Add user message to DB
            user_msg = AgentMessage(
                session_id=session_id,
                role="user",
                content=message,
            )
            db.add(user_msg)
            session.message_count += 1
            db.commit()

            # Execute agent using async generator
            full_content = ""
            tool_call_count = 0
            total_tokens = 0

            # Run the agent loop using async generator
            async for event in executor.run(message):
                # Check for cancellation at each iteration
                if check_cancel_flag(session_id):
                    logger.info(f"Session {session_id} cancelled during execution")
                    session.status = "cancelled"
                    db.commit()
                    publish_live_session_event("session_cancelled", {
                        "session_id": session_id,
                    })
                    return {"cancelled": True, "session_id": session_id}

                event_type = event.event_type

                if event_type == "content":
                    full_content += event.data.get("content", "")

                elif event_type == "tool_call":
                    tool_call_count += 1
                    # Log the tool call
                    action = AgentAction(
                        session_id=session_id,
                        action_type="tool_call",
                        tool_name=event.data.get("name"),
                        tool_input=event.data.get("arguments"),
                        status="executing",
                    )
                    db.add(action)
                    db.commit()

                elif event_type == "tool_result":
                    # Update the action with result
                    action = db.query(AgentAction).filter(
                        AgentAction.session_id == session_id,
                        AgentAction.tool_name == event.data.get("name"),
                        AgentAction.status == "executing",
                    ).order_by(AgentAction.id.desc()).first()

                    if action:
                        action.tool_output = {"result": event.data.get("result")}
                        action.status = "completed" if not event.data.get("error") else "failed"
                        action.error_message = event.data.get("error")
                        action.duration_ms = event.data.get("duration_ms")
                        db.commit()

                elif event_type == "approval_requested":
                    # Agent requested approval - wait for it
                    approval_id = event.data.get("approval_id")
                    if approval_id:
                        try:
                            logger.info(f"Session {session_id} waiting for approval {approval_id}")
                            # Update session status so dashboard shows waiting state
                            session.status = "waiting_approval"
                            db.commit()
                            wait_for_approval(session_id, approval_id)
                            # Resume execution - update status back to running
                            session.status = "running"
                            db.commit()
                            logger.info(f"Session {session_id} approval granted, continuing")
                        except TaskCancelled:
                            session.status = "cancelled"
                            db.commit()
                            return {"cancelled": True, "session_id": session_id}
                        except ApprovalRejected as e:
                            session.status = "failed"
                            db.commit()
                            return {"rejected": True, "reason": str(e), "session_id": session_id}
                        except ApprovalExpired:
                            session.status = "failed"
                            db.commit()
                            return {"expired": True, "session_id": session_id}

                elif event_type == "done":
                    usage = event.data.get("usage", {})
                    total_tokens += usage.get("total_tokens", 0)

                elif event_type == "error":
                    logger.error(f"Agent error: {event.data.get('error')}")

            # Save assistant response
            if full_content:
                assistant_msg = AgentMessage(
                    session_id=session_id,
                    role="assistant",
                    content=full_content,
                    token_count=total_tokens,
                )
                db.add(assistant_msg)
                session.message_count += 1

            # Update session stats
            session.tool_call_count += tool_call_count
            session.token_count += total_tokens
            session.status = "completed"
            session.completed_at = datetime.utcnow()
            db.commit()

            logger.info(
                f"Agent execution completed: session_id={session_id}, "
                f"tools={tool_call_count}, tokens={total_tokens}"
            )

            return {
                "success": True,
                "session_id": session_id,
                "response": full_content[:500] if full_content else None,
                "tool_calls": tool_call_count,
                "tokens": total_tokens,
            }

        except Exception as e:
            logger.exception(f"Agent execution failed: {e}")
            session.status = "failed"
            db.commit()
            raise


@shared_task(bind=True, max_retries=3)
def execute_agent_session(self, session_id: int, initial_message: str = None):
    """Execute an agent session asynchronously.

    Args:
        session_id: ID of the AgentSession to execute
        initial_message: Optional initial message to start the conversation

    This task is used for:
    - Webhook-triggered agent executions
    - Scheduled agent runs
    - Background agent tasks
    """
    try:
        # Run the async function in an event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _run_agent_session(session_id, initial_message)
            )
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Agent execution failed: {e}")
        # Retry on transient errors
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30)
        return {"error": str(e)}


@shared_task
def resume_agent_session(session_id: int, message: str):
    """Resume an agent session after approval or pause.

    Args:
        session_id: ID of the session to resume
        message: Message to continue the conversation
    """
    logger.info(f"Resuming agent session: session_id={session_id}")

    with get_db_context() as db:
        session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
        if not session:
            return {"error": "Session not found"}

        if session.status not in ("waiting_approval", "paused"):
            return {"error": f"Session cannot be resumed (status: {session.status})"}

        # Mark as running
        session.status = "running"
        db.commit()

    # Continue execution
    return execute_agent_session(session_id, message)
