"""Chat/Agent session routes with SSE streaming."""

import json
import asyncio
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Agent, AgentSession, AgentMessage, AgentAction
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType
from netagent_core.llm import GeminiClient

router = APIRouter()


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

    async def generate_response():
        """Generate streaming response."""
        try:
            # Get conversation history
            messages = db.query(AgentMessage).filter(
                AgentMessage.session_id == session_id
            ).order_by(AgentMessage.created_at).all()

            # Build message list for LLM
            llm_messages = [
                {"role": "system", "content": agent.system_prompt}
            ]
            for msg in messages:
                if msg.role in ["user", "assistant"]:
                    llm_messages.append({
                        "role": msg.role,
                        "content": msg.content or "",
                    })

            # Create Gemini client
            try:
                client = GeminiClient(model=agent.model)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
                return

            # Stream response
            full_content = ""

            # Send thinking event
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Processing your request...'})}\n\n"

            try:
                response = await client.achat(
                    messages=llm_messages,
                    temperature=agent.temperature,
                    max_tokens=agent.max_tokens,
                )

                if response.content:
                    full_content = response.content
                    # Send content in chunks for streaming effect
                    chunk_size = 50
                    for i in range(0, len(full_content), chunk_size):
                        chunk = full_content[i:i+chunk_size]
                        yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
                        await asyncio.sleep(0.02)

                # Handle tool calls if any
                if response.has_tool_calls:
                    for tc in response.tool_calls:
                        yield f"data: {json.dumps({'type': 'tool_call', 'name': tc.name, 'arguments': tc.arguments})}\n\n"

                # Send completion event
                yield f"data: {json.dumps({'type': 'done', 'usage': response.usage})}\n\n"

                # Save assistant message
                assistant_message = AgentMessage(
                    session_id=session_id,
                    role="assistant",
                    content=full_content,
                    token_count=response.usage.get("completion_tokens", 0),
                )
                db.add(assistant_message)
                session.message_count += 1
                session.token_count += response.usage.get("total_tokens", 0)
                db.commit()

            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
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
