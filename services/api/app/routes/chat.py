"""Chat/Agent session routes with SSE streaming and tool execution."""

import json
import asyncio
import os
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Agent, AgentSession, AgentMessage, AgentAction
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType
from netagent_core.redis_events import set_cancel_flag, publish_live_session_event
from netagent_core.llm import GeminiClient, AgentExecutor, ToolDefinition
from netagent_core.tools import create_ssh_tool, create_knowledge_search_tool

logger = logging.getLogger(__name__)

router = APIRouter()


def build_specialist_prompt(db: Session, current_agent_id: int, allowed_agent_ids: list = None) -> str:
    """Build system prompt section listing available specialist agents for handoff.

    Args:
        db: Database session
        current_agent_id: ID of the current agent (to exclude from list)
        allowed_agent_ids: If set, only include these agent IDs

    Returns:
        String to append to system prompt, or empty string if no specialists available
    """
    query = db.query(Agent).filter(
        Agent.is_template == False,
        Agent.enabled == True,
        Agent.id != current_agent_id,
    )

    if allowed_agent_ids:
        query = query.filter(Agent.id.in_(allowed_agent_ids))

    specialists = query.order_by(Agent.name).all()

    if not specialists:
        return ""

    lines = [
        "## Available Specialist Agents",
        "",
        "You can delegate tasks to the following specialist agents using the `handoff_to_agent` tool:",
        "",
    ]

    for agent in specialists:
        desc = agent.description or f"{agent.agent_type} specialist"
        lines.append(f"- **{agent.name}** (ID: {agent.id}): {desc}")

    lines.extend([
        "",
        "When you identify a task that matches a specialist's expertise, hand it off using:",
        "```",
        'handoff_to_agent(target_agent_id=<id>, task_summary="Brief description of what to do", context={...})',
        "```",
    ])

    return "\n".join(lines)


class SessionCreate(BaseModel):
    agent_id: int
    context: Optional[dict] = None


class MessageCreate(BaseModel):
    content: str


class SessionResponse(BaseModel):
    id: int
    agent_id: int
    status: str
    trigger_type: Optional[str]
    message_count: int
    tool_call_count: int
    token_count: int
    context: dict
    user_id: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: Optional[str]
    tool_calls: Optional[dict]
    tool_call_id: Optional[str]
    token_count: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class ActionResponse(BaseModel):
    id: int
    session_id: int
    action_type: str
    tool_name: Optional[str]
    tool_input: Optional[dict]
    tool_output: Optional[dict]
    reasoning: Optional[str]
    risk_level: Optional[str]
    requires_approval: bool
    status: str
    error_message: Optional[str]
    duration_ms: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


def build_tools_for_agent(
    agent: Agent,
    db_session_factory=None,
) -> List[ToolDefinition]:
    """Build tool definitions based on agent configuration.

    Args:
        agent: Agent configuration
        db_session_factory: Factory for DB sessions

    Returns:
        List of ToolDefinition objects
    """
    config = {
        "allowed_tools": agent.allowed_tools or [],
        "allowed_device_patterns": agent.allowed_device_patterns or ["*"],
        "knowledge_base_ids": agent.knowledge_base_ids or [],
        "mcp_server_ids": agent.mcp_server_ids or [],
    }
    return build_tools_for_agent_config(config, db_session_factory)


def build_tools_for_agent_config(
    config: dict,
    db_session_factory=None,
) -> List[ToolDefinition]:
    """Build tool definitions based on agent configuration dict.

    Args:
        config: Dict with allowed_tools, allowed_device_patterns, knowledge_base_ids, mcp_server_ids
        db_session_factory: Factory for DB sessions

    Returns:
        List of ToolDefinition objects
    """
    tools = []
    encryption_key = os.getenv("ENCRYPTION_KEY")

    # SSH command tool
    if "ssh_command" in config.get("allowed_tools", []):
        tools.append(create_ssh_tool(
            allowed_device_patterns=config.get("allowed_device_patterns", ["*"]),
            db_session_factory=db_session_factory,
            encryption_key=encryption_key,
        ))

    # Knowledge search tool
    if "search_knowledge" in config.get("allowed_tools", []) and config.get("knowledge_base_ids"):
        tools.append(create_knowledge_search_tool(
            knowledge_base_ids=config["knowledge_base_ids"],
            db_session_factory=db_session_factory,
        ))

    # Email tool
    if "send_email" in config.get("allowed_tools", []):
        from netagent_core.tools import create_email_tool
        tools.append(create_email_tool())
        logger.info("Added send_email tool")
    else:
        logger.debug(f"send_email not in allowed_tools: {config.get('allowed_tools', [])}")

    return tools


async def build_tools_for_agent_config_async(
    config: dict,
    db_session_factory=None,
    session_id: int = None,
    event_callback=None,
    handoff_depth: int = 0,
) -> List[ToolDefinition]:
    """Build tool definitions including MCP tools and handoff tool (async version).

    Args:
        config: Dict with allowed_tools, allowed_device_patterns, knowledge_base_ids, mcp_server_ids
        db_session_factory: Factory for DB sessions
        session_id: Current session ID (needed for handoff tool)
        event_callback: Async callback to emit events (for handoff tool)
        handoff_depth: Current nesting depth for handoffs

    Returns:
        List of ToolDefinition objects

    Note:
        Empty mcp_server_ids or knowledge_base_ids means "use none".
        Tools and resources must be explicitly selected per agent.
    """
    # Note: Empty mcp_server_ids or knowledge_base_ids means "use none"
    # Tools and resources must be explicitly selected per agent

    # Get sync tools first
    tools = build_tools_for_agent_config(config, db_session_factory)

    # Add handoff tool if enabled and session context available
    if "handoff_to_agent" in config.get("allowed_tools", []) and session_id and event_callback:
        from netagent_core.tools import create_handoff_tool

        handoff_tool = create_handoff_tool(
            db_session_factory=db_session_factory,
            parent_session_id=session_id,
            event_callback=event_callback,
            current_depth=handoff_depth,
            allowed_agent_ids=config.get("allowed_handoff_agent_ids"),
        )
        tools.append(ToolDefinition(
            name=handoff_tool.name,
            description=handoff_tool.description,
            parameters=handoff_tool.parameters,
            handler=handoff_tool.execute,
            requires_approval=handoff_tool.requires_approval,
            risk_level=handoff_tool.risk_level,
        ))
        logger.info("Added handoff_to_agent tool")

    # Add approval tool if enabled and session context available
    if "request_approval" in config.get("allowed_tools", []) and session_id:
        from netagent_core.tools import create_approval_tool

        approval_tool = create_approval_tool(
            db_session_factory=db_session_factory,
            session_id=session_id,
            event_callback=event_callback,
        )
        tools.append(approval_tool)
        logger.info("Added request_approval tool")

    # Add MCP tools if configured
    mcp_server_ids = config.get("mcp_server_ids", [])
    if mcp_server_ids and db_session_factory:
        from netagent_core.tools import load_mcp_tools_for_agent

        try:
            mcp_tools = await load_mcp_tools_for_agent(
                mcp_server_ids=mcp_server_ids,
                db_session_factory=db_session_factory,
            )
            tools.extend(mcp_tools)
            logger.info(f"Loaded {len(mcp_tools)} MCP tools")
        except Exception as e:
            logger.error(f"Failed to load MCP tools: {e}")

    return tools


class SessionListResponse(BaseModel):
    id: int
    agent_id: int
    agent_name: Optional[str] = None
    status: str
    trigger_type: Optional[str]
    message_count: int
    tool_call_count: int
    token_count: int
    user_id: Optional[int]
    user_email: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


@router.get("/sessions", response_model=dict)
async def list_sessions(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    status: Optional[str] = Query(default=None, description="Filter by status (active, completed)"),
    agent_id: Optional[int] = Query(default=None, description="Filter by agent ID"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """List all chat sessions with filtering options."""
    from netagent_core.db import User

    query = db.query(AgentSession).join(Agent, AgentSession.agent_id == Agent.id)

    if status:
        query = query.filter(AgentSession.status == status)
    if agent_id:
        query = query.filter(AgentSession.agent_id == agent_id)

    total = query.count()
    sessions = query.order_by(AgentSession.created_at.desc()).offset(offset).limit(limit).all()

    # Build response with agent names and user emails
    items = []
    for session in sessions:
        agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
        user_obj = db.query(User).filter(User.id == session.user_id).first() if session.user_id else None

        items.append(SessionListResponse(
            id=session.id,
            agent_id=session.agent_id,
            agent_name=agent.name if agent else None,
            status=session.status,
            trigger_type=session.trigger_type,
            message_count=session.message_count,
            tool_call_count=session.tool_call_count,
            token_count=session.token_count,
            user_id=session.user_id,
            user_email=user_obj.email if user_obj else None,
            created_at=session.created_at,
            completed_at=session.completed_at,
        ))

    return {
        "items": items,
        "total": total,
    }


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    data: SessionCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new chat session with an agent."""
    # Verify agent exists
    agent = db.query(Agent).filter(Agent.id == data.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")

    session = AgentSession(
        agent_id=data.agent_id,
        status="active",
        trigger_type="user",
        context=data.context or {},
        user_id=user.id,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    # Audit log
    audit_log(
        db,
        AuditEventType.AGENT_CHAT_STARTED,
        user=user,
        resource_type="agent_session",
        resource_id=session.id,
        resource_name=agent.name,
        action="create",
    )

    return SessionResponse.model_validate(session)


@router.get("/sessions/live/stream")
async def stream_live_sessions(
    user: ALBUser = Depends(get_current_user),
):
    """SSE stream for live session updates.

    Subscribes to the global sessions:live Redis channel and forwards events.
    """
    import redis
    from netagent_core.redis_events import REDIS_URL, SESSIONS_LIVE_CHANNEL

    async def event_generator():
        """Generate SSE events from Redis pub/sub."""
        # Create a separate Redis connection for subscription
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(SESSIONS_LIVE_CHANNEL)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Redis connection failed'})}\n\n"
            return

        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"

            while True:
                message = pubsub.get_message(timeout=1.0)
                if message and message["type"] == "message":
                    yield f"data: {message['data']}\n\n"

                # Small sleep to prevent busy loop
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            pass
        finally:
            pubsub.unsubscribe(SESSIONS_LIVE_CHANNEL)
            pubsub.close()
            r.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/sessions/live")
async def get_live_sessions(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get currently active/waiting sessions."""
    from netagent_core.db import User, Approval

    sessions = db.query(AgentSession).filter(
        AgentSession.status.in_(["active", "running", "waiting_approval"])
    ).order_by(AgentSession.created_at.desc()).all()

    items = []
    for session in sessions:
        agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
        user_obj = db.query(User).filter(User.id == session.user_id).first() if session.user_id else None

        # Get latest message for preview
        latest_msg = db.query(AgentMessage).filter(
            AgentMessage.session_id == session.id
        ).order_by(AgentMessage.created_at.desc()).first()

        # Get pending approval if waiting
        pending_approval = None
        if session.status == "waiting_approval":
            approval = db.query(Approval).filter(
                Approval.session_id == session.id,
                Approval.status == "pending"
            ).first()
            if approval:
                pending_approval = {
                    "id": approval.id,
                    "action_type": approval.action_type,
                    "action_description": approval.action_description,
                    "risk_level": approval.risk_level,
                }

        items.append({
            "id": session.id,
            "agent_id": session.agent_id,
            "agent_name": agent.name if agent else "Unknown",
            "status": session.status,
            "trigger_type": session.trigger_type,
            "user_email": user_obj.email if user_obj else None,
            "message_count": session.message_count,
            "tool_call_count": session.tool_call_count,
            "created_at": session.created_at.isoformat(),
            "latest_message": latest_msg.content[:200] if latest_msg and latest_msg.content else None,
            "pending_approval": pending_approval,
        })

    return {"items": items, "count": len(items)}


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get session details."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse.model_validate(session)


@router.get("/sessions/{session_id}/messages", response_model=dict)
async def get_session_messages(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """Get messages in a session."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    query = db.query(AgentMessage).filter(AgentMessage.session_id == session_id)
    total = query.count()
    messages = query.order_by(AgentMessage.created_at).offset(offset).limit(limit).all()

    return {
        "items": [MessageResponse.model_validate(m) for m in messages],
        "total": total,
    }


@router.get("/sessions/{session_id}/actions", response_model=dict)
async def get_session_actions(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """Get action audit trail for a session."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    query = db.query(AgentAction).filter(AgentAction.session_id == session_id)
    total = query.count()
    actions = query.order_by(AgentAction.created_at).offset(offset).limit(limit).all()

    return {
        "items": [ActionResponse.model_validate(a) for a in actions],
        "total": total,
    }


@router.post("/sessions/{session_id}/message")
async def send_message(
    session_id: int,
    data: MessageCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Send a message and stream the response (SSE)."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Save user message
    user_message = AgentMessage(
        session_id=session_id,
        role="user",
        content=data.content,
    )
    db.add(user_message)
    session.message_count += 1
    db.commit()

    # Get database session factory for tools
    from netagent_core.db import get_db_context
    db_session_factory = get_db_context

    # Extract agent configuration before starting async generator
    # (prevents SQLAlchemy session detachment issues)
    agent_config = {
        "model": agent.model,
        "system_prompt": agent.system_prompt,
        "max_iterations": agent.max_iterations,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "allowed_tools": agent.allowed_tools or [],
        "allowed_device_patterns": agent.allowed_device_patterns or ["*"],
        "knowledge_base_ids": agent.knowledge_base_ids or [],
        "mcp_server_ids": agent.mcp_server_ids or [],
        "allowed_handoff_agent_ids": agent.allowed_handoff_agent_ids,
    }
    logger.info(f"Agent config allowed_tools: {agent_config['allowed_tools']}")

    # If handoff is enabled, build list of available specialists for system prompt
    if "handoff_to_agent" in agent_config["allowed_tools"]:
        specialist_info = build_specialist_prompt(db, agent.id, agent.allowed_handoff_agent_ids)
        if specialist_info:
            agent_config["system_prompt"] = agent_config["system_prompt"] + "\n\n" + specialist_info

    # Create event queue for handoff tool events
    event_queue = asyncio.Queue()

    # Get conversation history for context (before generator)
    history_query = db.query(AgentMessage).filter(
        AgentMessage.session_id == session_id,
        AgentMessage.role.in_(["user", "assistant"])
    ).order_by(AgentMessage.created_at).all()

    history_messages = []
    for msg in history_query[:-1]:  # Exclude the message we just added
        if msg.content:
            history_messages.append({
                "role": msg.role,
                "content": msg.content,
            })

    async def generate_response():
        """Generate streaming response using agent executor."""
        nonlocal db

        # Event callback for handoff tool - puts events on queue
        async def emit_event(event):
            await event_queue.put(event)

        try:
            # Create Gemini client
            try:
                client = GeminiClient(model=agent_config["model"])
            except Exception as e:
                logger.error(f"Failed to create Gemini client: {e}")
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
                return

            # Build tools based on agent configuration (including MCP and handoff tools)
            tools = await build_tools_for_agent_config_async(
                agent_config,
                db_session_factory,
                session_id=session_id,
                event_callback=emit_event,
            )

            # Create agent executor
            executor = AgentExecutor(
                client=client,
                system_prompt=agent_config["system_prompt"],
                tools=tools,
                max_iterations=agent_config["max_iterations"],
                temperature=agent_config["temperature"],
                max_tokens=agent_config["max_tokens"],
            )

            # Add history to executor
            executor.messages = history_messages

            # Track accumulated content and actions (to save in batch at end)
            full_content = ""
            total_tokens = 0
            tool_call_count = 0
            pending_actions = []  # Collect actions to save at end

            # Helper to drain handoff events from queue
            async def drain_handoff_events():
                """Yield any pending handoff events from the queue."""
                events_to_yield = []
                while not event_queue.empty():
                    try:
                        handoff_event = event_queue.get_nowait()
                        events_to_yield.append(handoff_event)
                    except asyncio.QueueEmpty:
                        break
                return events_to_yield

            # Run agent and stream events
            async for event in executor.run(data.content):
                # First, drain any pending handoff events (emitted during tool execution)
                for handoff_event in await drain_handoff_events():
                    event_data = {
                        "type": handoff_event.event_type,
                        **handoff_event.data
                    }
                    yield f"data: {json.dumps(event_data)}\n\n"

                if event.event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'content': event.data.get('message', 'Processing...')})}\n\n"

                elif event.event_type == "reasoning":
                    # Agent's reasoning/thought process
                    reasoning = event.data.get("content", "")
                    yield f"data: {json.dumps({'type': 'reasoning', 'content': reasoning})}\n\n"

                    # Queue action for later save
                    pending_actions.append({
                        "action_type": "thought",
                        "reasoning": reasoning,
                    })

                elif event.event_type == "tool_call":
                    tool_data = {
                        "type": "tool_call",
                        "name": event.data.get("name"),
                        "arguments": event.data.get("arguments"),
                        "risk_level": event.data.get("risk_level", "low"),
                    }
                    yield f"data: {json.dumps(tool_data)}\n\n"

                    # Queue action for later save
                    pending_actions.append({
                        "action_type": "tool_call",
                        "tool_name": event.data.get("name"),
                        "tool_input": event.data.get("arguments"),
                        "risk_level": event.data.get("risk_level"),
                        "requires_approval": event.data.get("requires_approval", False),
                        "status": "running",
                    })
                    tool_call_count += 1

                elif event.event_type == "tool_result":
                    result_data = {
                        "type": "tool_result",
                        "name": event.data.get("name"),
                        "result": event.data.get("result"),
                        "error": event.data.get("error"),
                        "duration_ms": event.data.get("duration_ms"),
                    }
                    yield f"data: {json.dumps(result_data)}\n\n"

                    # Queue action for later save
                    pending_actions.append({
                        "action_type": "tool_result",
                        "tool_name": event.data.get("name"),
                        "tool_output": {"result": event.data.get("result")} if event.data.get("result") else None,
                        "error_message": event.data.get("error"),
                        "duration_ms": event.data.get("duration_ms"),
                        "status": "completed" if not event.data.get("error") else "failed",
                    })

                elif event.event_type == "content":
                    content = event.data.get("content", "")
                    full_content += content

                    # Stream content in chunks for better UX
                    chunk_size = 50
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i:i+chunk_size]
                        yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
                        await asyncio.sleep(0.01)

                elif event.event_type == "done":
                    total_tokens = event.data.get("usage", {}).get("total_tokens", 0)
                    yield f"data: {json.dumps({'type': 'done', 'usage': event.data.get('usage', {})})}\n\n"

                elif event.event_type == "error":
                    error = event.data.get("error", "Unknown error")
                    yield f"data: {json.dumps({'type': 'error', 'error': error})}\n\n"

                    # Queue error action for later save
                    pending_actions.append({
                        "action_type": "error",
                        "error_message": error,
                        "status": "failed",
                    })

                elif event.event_type == "approval_required":
                    yield f"data: {json.dumps({'type': 'approval_required', 'tool_name': event.data.get('tool_name'), 'arguments': event.data.get('arguments'), 'risk_level': event.data.get('risk_level')})}\n\n"

            # Save all data using a fresh DB session
            with db_session_factory() as fresh_db:
                # Save pending actions
                for action_data in pending_actions:
                    action = AgentAction(session_id=session_id, **action_data)
                    fresh_db.add(action)

                # Save assistant message
                if full_content:
                    assistant_message = AgentMessage(
                        session_id=session_id,
                        role="assistant",
                        content=full_content,
                        token_count=total_tokens,
                    )
                    fresh_db.add(assistant_message)

                # Update session stats
                fresh_session = fresh_db.query(AgentSession).filter(
                    AgentSession.id == session_id
                ).first()
                if fresh_session:
                    fresh_session.message_count += 1
                    fresh_session.token_count += total_tokens
                    fresh_session.tool_call_count += tool_call_count

                fresh_db.commit()

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/sessions/{session_id}/stop")
async def stop_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Stop a running session."""
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.status = "completed"
    session.completed_at = datetime.utcnow()
    db.commit()

    # Audit log
    agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
    audit_log(
        db,
        AuditEventType.AGENT_CHAT_COMPLETED,
        user=user,
        resource_type="agent_session",
        resource_id=session.id,
        resource_name=agent.name if agent else "Unknown",
        action="stop",
    )

    return {"message": "Session stopped"}


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Cancel a running or waiting session.

    Sets a Redis flag that the worker checks during execution.
    """
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("active", "running", "waiting_approval"):
        raise HTTPException(
            status_code=400,
            detail=f"Session cannot be cancelled (status: {session.status})"
        )

    # Set cancel flag in Redis for worker to check
    set_cancel_flag(session_id)

    # Update session status
    session.status = "cancelled"
    session.completed_at = datetime.utcnow()
    db.commit()

    # Publish event for frontend
    publish_live_session_event("session_cancelled", {
        "session_id": session_id,
        "cancelled_by": user.email,
    })

    # Audit log
    agent = db.query(Agent).filter(Agent.id == session.agent_id).first()
    audit_log(
        db,
        AuditEventType.AGENT_CHAT_COMPLETED,
        user=user,
        resource_type="agent_session",
        resource_id=session.id,
        resource_name=agent.name if agent else "Unknown",
        action="cancel",
        details={"cancelled_by": user.email},
    )

    return {"message": "Session cancelled", "session_id": session_id}


@router.get("/available-tools")
async def get_available_tools(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get list of available tools for agents."""
    from netagent_core.db import MCPServer

    # Built-in tools
    builtin_tools = [
        {
            "name": "ssh_command",
            "description": "Execute read-only SSH commands on network devices",
            "category": "network",
            "requires_config": ["allowed_device_patterns"],
        },
        {
            "name": "search_knowledge",
            "description": "Search knowledge bases for relevant documentation",
            "category": "knowledge",
            "requires_config": ["knowledge_base_ids"],
        },
        {
            "name": "handoff_to_agent",
            "description": "Hand off tasks to specialist agents for focused problem solving",
            "category": "orchestration",
            "requires_config": [],
        },
    ]

    # Get MCP servers with their tools
    mcp_servers = db.query(MCPServer).filter(MCPServer.enabled == True).all()
    mcp_tools = []

    for server in mcp_servers:
        for tool in (server.tools or []):
            mcp_tools.append({
                "name": f"mcp_{server.name}_{tool.get('name', 'unknown')}",
                "description": tool.get("description", "MCP tool"),
                "category": "mcp",
                "mcp_server_id": server.id,
                "mcp_server_name": server.name,
                "original_name": tool.get("name"),
            })

    return {
        "tools": builtin_tools,
        "mcp_tools": mcp_tools,
        "mcp_servers": [
            {"id": s.id, "name": s.name, "health_status": s.health_status}
            for s in mcp_servers
        ],
    }
