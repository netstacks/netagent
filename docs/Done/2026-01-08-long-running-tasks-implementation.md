# Long-Running Tasks & Session Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement end-to-end approval workflows, live session monitoring, session cancellation, and explicit tool/knowledge selection for agents.

**Architecture:** Redis Pub/Sub for real-time events, Celery workers with approval polling, SSE streams for frontend updates, explicit tool/knowledge selection replacing the ALL_TOOLS default.

**Tech Stack:** FastAPI, Celery, Redis, PostgreSQL, Bootstrap 5, SSE (Server-Sent Events)

---

## Task 1: Create Redis Events Utility Module

**Files:**
- Create: `shared/netagent_core/redis_events.py`
- Modify: `shared/netagent_core/__init__.py`

**Step 1: Write the redis events utility**

```python
# shared/netagent_core/redis_events.py
"""Redis pub/sub utilities for real-time session events."""

import json
import logging
import os
from typing import Optional, Callable, Any
from contextlib import asynccontextmanager

import redis
from redis import Redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Channel patterns
SESSION_EVENTS_CHANNEL = "session:{session_id}:events"
SESSIONS_LIVE_CHANNEL = "sessions:live"
SESSION_CANCEL_KEY = "session:{session_id}:cancel_flag"


def get_redis_client() -> Redis:
    """Get a Redis client instance."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def publish_session_event(session_id: int, event_type: str, data: dict) -> None:
    """Publish an event for a specific session.

    Args:
        session_id: The session ID
        event_type: Type of event (e.g., 'approval_resolved', 'progress', 'completed')
        data: Event data
    """
    client = get_redis_client()
    channel = SESSION_EVENTS_CHANNEL.format(session_id=session_id)
    message = json.dumps({"type": event_type, **data})
    client.publish(channel, message)
    logger.debug(f"Published {event_type} to {channel}")


def publish_live_session_event(event_type: str, data: dict) -> None:
    """Publish an event to the global live sessions channel.

    Args:
        event_type: Type of event (e.g., 'session_started', 'session_completed')
        data: Event data including session_id
    """
    client = get_redis_client()
    message = json.dumps({"type": event_type, **data})
    client.publish(SESSIONS_LIVE_CHANNEL, message)
    logger.debug(f"Published {event_type} to {SESSIONS_LIVE_CHANNEL}")


def set_cancel_flag(session_id: int, ttl_seconds: int = 3600) -> None:
    """Set the cancellation flag for a session.

    Args:
        session_id: The session ID
        ttl_seconds: Time-to-live for the flag (default 1 hour)
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    client.set(key, "1", ex=ttl_seconds)
    logger.info(f"Set cancel flag for session {session_id}")


def check_cancel_flag(session_id: int) -> bool:
    """Check if a session has been cancelled.

    Args:
        session_id: The session ID

    Returns:
        True if session is cancelled, False otherwise
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    return client.get(key) is not None


def clear_cancel_flag(session_id: int) -> None:
    """Clear the cancellation flag for a session.

    Args:
        session_id: The session ID
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    client.delete(key)
```

**Step 2: Export from netagent_core __init__.py**

Add to `shared/netagent_core/__init__.py`:

```python
from .redis_events import (
    get_redis_client,
    publish_session_event,
    publish_live_session_event,
    set_cancel_flag,
    check_cancel_flag,
    clear_cancel_flag,
)
```

**Step 3: Test manually by importing**

Run: `cd /home/cwdavis/scripts/netagent && python3 -c "from netagent_core.redis_events import get_redis_client; print('OK')"`
Expected: OK (or import error if Redis not installed - that's fine for now)

**Step 4: Commit**

```bash
git add shared/netagent_core/redis_events.py shared/netagent_core/__init__.py
git commit -m "$(cat <<'EOF'
feat: add Redis events utility for session pub/sub

Add redis_events.py with utilities for:
- Publishing session-specific events
- Publishing global live session events
- Managing session cancellation flags

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add Session Cancellation to Approval API

**Files:**
- Modify: `services/api/app/routes/approvals.py:116-127` (approve endpoint)
- Modify: `services/api/app/routes/approvals.py:157-161` (reject endpoint)

**Step 1: Import redis_events in approvals.py**

Add at top of `services/api/app/routes/approvals.py`:

```python
from netagent_core.redis_events import publish_session_event, publish_live_session_event
```

**Step 2: Add Redis publish on approval**

In `approve_action` function, after line 127 (after `db.commit()` for session status update), add:

```python
            # Publish approval resolved event
            publish_session_event(approval.session_id, "approval_resolved", {
                "approval_id": approval.id,
                "status": "approved",
                "resolved_by": user.email,
            })
            publish_live_session_event("session_resumed", {
                "session_id": approval.session_id,
            })
```

**Step 3: Add Redis publish on rejection**

In `reject_action` function, after line 168 (after `db.commit()` for session status update), add:

```python
            # Publish rejection event
            publish_session_event(approval.session_id, "approval_resolved", {
                "approval_id": approval.id,
                "status": "rejected",
                "resolved_by": user.email,
                "note": data.note,
            })
            publish_live_session_event("session_failed", {
                "session_id": approval.session_id,
                "reason": "Approval rejected",
            })
```

**Step 4: Verify syntax**

Run: `python3 -m py_compile services/api/app/routes/approvals.py`
Expected: No output (success)

**Step 5: Commit**

```bash
git add services/api/app/routes/approvals.py
git commit -m "$(cat <<'EOF'
feat: publish Redis events on approval resolution

When approvals are granted or rejected, publish events to:
- Session-specific channel for worker polling
- Global live sessions channel for dashboard updates

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add Session Cancel Endpoint

**Files:**
- Modify: `services/api/app/routes/chat.py` (add cancel endpoint)

**Step 1: Add imports**

Add to imports in `services/api/app/routes/chat.py`:

```python
from netagent_core.redis_events import set_cancel_flag, publish_live_session_event
```

**Step 2: Add cancel endpoint**

Add after the `stop_session` endpoint (around line 753):

```python
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
```

**Step 3: Verify syntax**

Run: `python3 -m py_compile services/api/app/routes/chat.py`
Expected: No output (success)

**Step 4: Commit**

```bash
git add services/api/app/routes/chat.py
git commit -m "$(cat <<'EOF'
feat: add session cancellation endpoint

POST /api/chat/sessions/{id}/cancel now:
- Sets Redis cancel flag for worker to check
- Updates session status to 'cancelled'
- Publishes live session event
- Logs cancellation in audit log

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add Live Sessions SSE Endpoint

**Files:**
- Modify: `services/api/app/routes/chat.py` (add SSE stream endpoint)

**Step 1: Add live sessions SSE endpoint**

Add after the cancel endpoint:

```python
@router.get("/sessions/live/stream")
async def stream_live_sessions(
    db: Session = Depends(get_db),
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
        r = redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        pubsub.subscribe(SESSIONS_LIVE_CHANNEL)

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
    from netagent_core.db import User

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
            from netagent_core.db import Approval
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
```

**Step 2: Add REDIS_URL export to redis_events.py**

Ensure this line is present in `shared/netagent_core/redis_events.py`:
```python
# Already defined as: REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Make sure it's exported
```

**Step 3: Verify syntax**

Run: `python3 -m py_compile services/api/app/routes/chat.py`
Expected: No output (success)

**Step 4: Commit**

```bash
git add services/api/app/routes/chat.py
git commit -m "$(cat <<'EOF'
feat: add live sessions endpoints

- GET /api/chat/sessions/live - list active/waiting sessions
- GET /api/chat/sessions/live/stream - SSE for real-time updates

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add Approval Polling to Worker

**Files:**
- Modify: `services/worker/app/tasks/agent_executor.py`

**Step 1: Add exception classes and imports**

Add near top of file after existing imports:

```python
from netagent_core.redis_events import check_cancel_flag, publish_live_session_event
import time


class TaskCancelled(Exception):
    """Raised when a task is cancelled by the user."""
    pass


class ApprovalRejected(Exception):
    """Raised when an approval request is rejected."""
    pass


class ApprovalExpired(Exception):
    """Raised when an approval request expires."""
    pass
```

**Step 2: Add approval wait function**

Add after the exception classes:

```python
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
```

**Step 3: Modify _run_agent_session to check cancellation**

In the `_run_agent_session` function, add cancellation check at the start of the tool event loop. Find the line `async for event in executor.run(message):` and modify the loop to:

```python
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
                # ... rest of event handling unchanged
```

**Step 4: Handle approval_requested event**

In the event loop, add handling for approval events. After the `elif event_type == "tool_result":` block, add:

```python
                elif event_type == "approval_requested":
                    # Agent requested approval - wait for it
                    approval_id = event.data.get("approval_id")
                    if approval_id:
                        try:
                            logger.info(f"Session {session_id} waiting for approval {approval_id}")
                            wait_for_approval(session_id, approval_id)
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
```

**Step 5: Verify syntax**

Run: `python3 -m py_compile services/worker/app/tasks/agent_executor.py`
Expected: No output (success)

**Step 6: Commit**

```bash
git add services/worker/app/tasks/agent_executor.py
git commit -m "$(cat <<'EOF'
feat: add approval polling and cancellation to worker

- Add wait_for_approval() that polls DB every 5 seconds
- Check cancellation flag at each iteration of agent loop
- Handle approval_requested events by blocking until resolved
- Properly update session status on cancel/reject/expire

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Revert ALL_TOOLS Default in API

**Files:**
- Modify: `services/api/app/routes/agents.py`

**Step 1: Remove ALL_TOOLS constant**

Remove or comment out lines 16-17:

```python
# REMOVED: All agents get all tools enabled by default
# ALL_TOOLS = ['ssh_command', 'search_knowledge', 'handoff_to_agent', 'request_approval', 'send_email']
```

**Step 2: Update create_agent to use provided tools**

Replace lines 198-217 in `create_agent`:

```python
    # Use explicitly provided tools, knowledge bases, and MCP servers
    agent = Agent(
        name=data.name,
        description=data.description,
        agent_type=data.agent_type,
        system_prompt=data.system_prompt,
        model=data.model,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        max_iterations=data.max_iterations,
        autonomy_level=data.autonomy_level,
        allowed_tools=data.allowed_tools,  # Use provided tools
        allowed_device_patterns=data.allowed_device_patterns,
        mcp_server_ids=data.mcp_server_ids,  # Use provided MCP servers
        knowledge_base_ids=data.knowledge_base_ids,  # Use provided knowledge bases
        allowed_handoff_agent_ids=data.allowed_handoff_agent_ids,
        enabled=data.enabled,
        created_by=user.id,
    )
```

**Step 3: Update update_agent to use provided tools**

Replace lines 252-259 in `update_agent`:

```python
    # Update fields
    update_data = data.model_dump(exclude_unset=True)

    # No longer override tools/devices - use what's provided
    for key, value in update_data.items():
        setattr(agent, key, value)
```

**Step 4: Update duplicate_agent to copy original tools**

Replace lines 326-343 in `duplicate_agent`:

```python
    new_agent = Agent(
        name=f"{agent.name} (Copy)",
        description=agent.description,
        agent_type=agent.agent_type,
        system_prompt=agent.system_prompt,
        model=agent.model,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        max_iterations=agent.max_iterations,
        autonomy_level=agent.autonomy_level,
        allowed_tools=agent.allowed_tools,  # Copy original tools
        allowed_device_patterns=agent.allowed_device_patterns,
        mcp_server_ids=agent.mcp_server_ids,  # Copy original MCP servers
        knowledge_base_ids=agent.knowledge_base_ids,  # Copy original knowledge bases
        allowed_handoff_agent_ids=agent.allowed_handoff_agent_ids,
        enabled=False,
        created_by=user.id,
    )
```

**Step 5: Verify syntax**

Run: `python3 -m py_compile services/api/app/routes/agents.py`
Expected: No output (success)

**Step 6: Commit**

```bash
git add services/api/app/routes/agents.py
git commit -m "$(cat <<'EOF'
feat: revert ALL_TOOLS default - explicit tool selection

Agents now require explicit selection of:
- Tools (checkboxes)
- Knowledge bases (multi-select)
- MCP servers (multi-select)

This reduces overhead for specialized agents that don't need
all capabilities.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Remove "Empty = All" Logic from chat.py

**Files:**
- Modify: `services/api/app/routes/chat.py:218-238`

**Step 1: Remove auto-populate logic**

In `build_tools_for_agent_config_async`, remove the block that auto-populates empty lists. Replace lines 218-238 with:

```python
    # Note: Empty mcp_server_ids or knowledge_base_ids means "use none"
    # Tools and resources must be explicitly selected per agent

    # Get sync tools first
    tools = build_tools_for_agent_config(config, db_session_factory)
```

Remove the entire `if db_session_factory:` block that was there before (lines 223-237).

**Step 2: Verify syntax**

Run: `python3 -m py_compile services/api/app/routes/chat.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add services/api/app/routes/chat.py
git commit -m "$(cat <<'EOF'
refactor: remove empty=all auto-populate logic

Empty knowledge_base_ids and mcp_server_ids now mean "use none"
instead of "use all". Resources must be explicitly selected.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Remove "Empty = All" Logic from Worker

**Files:**
- Modify: `services/worker/app/tasks/agent_executor.py:31-59`

**Step 1: Remove auto-populate in build_tools_for_agent**

In `build_tools_for_agent`, replace lines 50-59 with:

```python
    # Knowledge search tool - only if knowledge bases are specified
    if "search_knowledge" in allowed_tools and agent.knowledge_base_ids:
        tools.append(create_knowledge_search_tool(
            knowledge_base_ids=agent.knowledge_base_ids,
            db_session_factory=db_session_factory,
        ))
```

Remove the "If empty, get all knowledge bases" logic that was there before.

**Step 2: Update docstring**

Update the docstring at line 31-33 to:

```python
    """Build tools for agent execution.

    This is a simplified version that doesn't include MCP tools
    (since they require async operation).

    Note:
        Empty knowledge_base_ids means "use no knowledge bases".
        Resources must be explicitly configured per agent.
    """
```

**Step 3: Verify syntax**

Run: `python3 -m py_compile services/worker/app/tasks/agent_executor.py`
Expected: No output (success)

**Step 4: Commit**

```bash
git add services/worker/app/tasks/agent_executor.py
git commit -m "$(cat <<'EOF'
refactor: remove empty=all logic from worker

Worker now respects explicit tool/knowledge selection.
Empty lists mean "use none" not "use all".

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Update Agent Create UI with Tool Checkboxes

**Files:**
- Modify: `services/frontend/app/templates/agent_create.html`

**Step 1: Add Tools Selection Card**

After the Autonomy Level Card (around line 139), add:

```html
            <!-- Tools Selection Card -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5 class="mb-0"><i class="bi bi-tools me-2"></i>Tools</h5>
                </div>
                <div class="card-body">
                    <div class="form-check mb-2">
                        <input class="form-check-input tool-checkbox" type="checkbox" id="tool-ssh" value="ssh_command">
                        <label class="form-check-label" for="tool-ssh">
                            <strong>SSH Command</strong>
                            <br><small class="text-muted">Execute read-only commands on network devices</small>
                        </label>
                    </div>
                    <div class="form-check mb-2">
                        <input class="form-check-input tool-checkbox" type="checkbox" id="tool-knowledge" value="search_knowledge">
                        <label class="form-check-label" for="tool-knowledge">
                            <strong>Search Knowledge</strong>
                            <br><small class="text-muted">Search knowledge bases for documentation</small>
                        </label>
                    </div>
                    <div class="form-check mb-2">
                        <input class="form-check-input tool-checkbox" type="checkbox" id="tool-handoff" value="handoff_to_agent">
                        <label class="form-check-label" for="tool-handoff">
                            <strong>Handoff to Agent</strong>
                            <br><small class="text-muted">Delegate tasks to specialist agents</small>
                        </label>
                    </div>
                    <div class="form-check mb-2">
                        <input class="form-check-input tool-checkbox" type="checkbox" id="tool-approval" value="request_approval">
                        <label class="form-check-label" for="tool-approval">
                            <strong>Request Approval</strong>
                            <br><small class="text-muted">Request human approval for risky actions</small>
                        </label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input tool-checkbox" type="checkbox" id="tool-email" value="send_email">
                        <label class="form-check-label" for="tool-email">
                            <strong>Send Email</strong>
                            <br><small class="text-muted">Send email notifications</small>
                        </label>
                    </div>
                </div>
            </div>

            <!-- Knowledge Bases Card -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5 class="mb-0"><i class="bi bi-book me-2"></i>Knowledge Bases</h5>
                </div>
                <div class="card-body">
                    <select class="form-select" id="agent-knowledge-bases" multiple size="4">
                        <!-- Loaded dynamically -->
                    </select>
                    <div class="form-text">Select knowledge bases this agent can search. Hold Ctrl/Cmd to select multiple.</div>
                </div>
            </div>

            <!-- MCP Servers Card -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5 class="mb-0"><i class="bi bi-plug me-2"></i>MCP Servers</h5>
                </div>
                <div class="card-body">
                    <select class="form-select" id="agent-mcp-servers" multiple size="4">
                        <!-- Loaded dynamically -->
                    </select>
                    <div class="form-text">Select MCP servers this agent can use. Hold Ctrl/Cmd to select multiple.</div>
                </div>
            </div>
```

**Step 2: Update populateForm function**

Add to the `populateForm` function:

```javascript
    // Tool checkboxes
    document.querySelectorAll('.tool-checkbox').forEach(cb => {
        cb.checked = (agent.allowed_tools || []).includes(cb.value);
    });

    // Knowledge bases multi-select
    const kbSelect = document.getElementById('agent-knowledge-bases');
    Array.from(kbSelect.options).forEach(opt => {
        opt.selected = (agent.knowledge_base_ids || []).includes(parseInt(opt.value));
    });

    // MCP servers multi-select
    const mcpSelect = document.getElementById('agent-mcp-servers');
    Array.from(mcpSelect.options).forEach(opt => {
        opt.selected = (agent.mcp_server_ids || []).includes(parseInt(opt.value));
    });
```

**Step 3: Update loadKnowledgeBases to populate select**

Replace the `loadKnowledgeBases` function:

```javascript
async function loadKnowledgeBases() {
    try {
        const response = await api.get('/api/knowledge');
        knowledgeBases = response.items || response || [];

        const select = document.getElementById('agent-knowledge-bases');
        select.innerHTML = knowledgeBases.map(kb =>
            `<option value="${kb.id}">${escapeHtml(kb.name)}</option>`
        ).join('');
    } catch (error) {
        console.error('Failed to load knowledge bases:', error);
    }
}
```

**Step 4: Update loadMCPServers to populate select**

Replace the `loadMCPServers` function:

```javascript
async function loadMCPServers() {
    try {
        const response = await api.get('/api/mcp/servers');
        mcpServers = response.items || response || [];

        const select = document.getElementById('agent-mcp-servers');
        select.innerHTML = mcpServers.map(server =>
            `<option value="${server.id}">${escapeHtml(server.name)}</option>`
        ).join('');
    } catch (error) {
        console.error('Failed to load MCP servers:', error);
    }
}
```

**Step 5: Update saveAgent to use checkboxes/selects**

Replace the data collection in `saveAgent`:

```javascript
    // Collect selected tools from checkboxes
    const allowedTools = [];
    document.querySelectorAll('.tool-checkbox:checked').forEach(cb => {
        allowedTools.push(cb.value);
    });

    // Collect selected knowledge bases
    const kbSelect = document.getElementById('agent-knowledge-bases');
    const knowledgeBaseIds = Array.from(kbSelect.selectedOptions).map(opt => parseInt(opt.value));

    // Collect selected MCP servers
    const mcpSelect = document.getElementById('agent-mcp-servers');
    const mcpServerIds = Array.from(mcpSelect.selectedOptions).map(opt => parseInt(opt.value));

    const data = {
        name: document.getElementById('agent-name').value,
        agent_type: document.getElementById('agent-type').value,
        description: document.getElementById('agent-description').value,
        system_prompt: document.getElementById('agent-prompt').value,
        model: document.getElementById('agent-model').value,
        temperature: parseFloat(document.getElementById('agent-temperature').value),
        max_tokens: parseInt(document.getElementById('agent-max-tokens').value),
        max_iterations: parseInt(document.getElementById('agent-max-iterations').value),
        autonomy_level: document.querySelector('input[name="autonomy"]:checked').value,
        allowed_tools: allowedTools,
        knowledge_base_ids: knowledgeBaseIds,
        mcp_server_ids: mcpServerIds,
        allowed_device_patterns: ['*'],
        enabled: document.getElementById('agent-enabled').checked
    };
```

**Step 6: Update testAgent similarly**

Apply the same data collection changes to the `testAgent` function.

**Step 7: Commit**

```bash
git add services/frontend/app/templates/agent_create.html
git commit -m "$(cat <<'EOF'
feat: add explicit tool/knowledge selection to agent UI

- Tool checkboxes for each available tool
- Multi-select dropdown for knowledge bases
- Multi-select dropdown for MCP servers
- Removed auto-all behavior

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Create Live Sessions Dashboard

**Files:**
- Create: `services/frontend/app/templates/live_sessions.html`
- Modify: `services/frontend/app/main.py` (add route)
- Modify: `services/frontend/app/templates/base.html` (add nav link)

**Step 1: Create live_sessions.html template**

```html
{% extends "base.html" %}

{% block title %}Live Sessions - NetAgent{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
    <div>
        <h2 class="mb-1">Live Sessions</h2>
        <p class="text-muted mb-0">Monitor active agent sessions in real-time</p>
    </div>
    <div>
        <span class="badge bg-success me-2" id="connection-status">
            <i class="bi bi-wifi"></i> Connected
        </span>
        <button class="btn btn-outline-secondary" id="btn-refresh">
            <i class="bi bi-arrow-clockwise"></i> Refresh
        </button>
    </div>
</div>

<!-- Session Cards -->
<div class="row g-4" id="sessions-grid">
    <!-- Sessions loaded dynamically -->
</div>

<!-- Empty State -->
<div id="sessions-empty" class="text-center py-5 d-none">
    <i class="bi bi-activity display-1 text-muted"></i>
    <h4 class="mt-3">No Active Sessions</h4>
    <p class="text-muted">All sessions have completed. Start a new conversation with an agent.</p>
    <a href="/agents" class="btn btn-primary">
        <i class="bi bi-robot me-1"></i> View Agents
    </a>
</div>

<!-- Session Detail Modal -->
<div class="modal fade" id="sessionModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">Session Details</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body" id="session-detail">
                <!-- Loaded dynamically -->
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
let sessions = [];
let eventSource = null;

document.addEventListener('DOMContentLoaded', function() {
    loadSessions();
    connectSSE();

    document.getElementById('btn-refresh').addEventListener('click', loadSessions);
});

async function loadSessions() {
    try {
        const response = await api.get('/api/chat/sessions/live');
        sessions = response.items || [];
        renderSessions();
    } catch (error) {
        showToast('Failed to load sessions', 'error');
    }
}

function renderSessions() {
    const grid = document.getElementById('sessions-grid');
    const empty = document.getElementById('sessions-empty');

    if (!sessions || sessions.length === 0) {
        grid.innerHTML = '';
        empty.classList.remove('d-none');
        return;
    }

    empty.classList.add('d-none');
    grid.innerHTML = sessions.map(session => {
        const statusBadge = getStatusBadge(session.status);
        const timeAgo = formatTimeAgo(session.created_at);

        return `
            <div class="col-md-6 col-lg-4">
                <div class="card h-100 session-card" data-session-id="${session.id}">
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-start mb-3">
                            <div>
                                ${statusBadge}
                                <h6 class="mt-2 mb-0">${escapeHtml(session.agent_name)}</h6>
                            </div>
                            <small class="text-muted">${timeAgo}</small>
                        </div>

                        ${session.latest_message ? `
                            <p class="text-muted small mb-3" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                                "${escapeHtml(session.latest_message)}"
                            </p>
                        ` : ''}

                        ${session.pending_approval ? `
                            <div class="alert alert-warning py-2 mb-3">
                                <small><strong>Awaiting Approval:</strong> ${escapeHtml(session.pending_approval.action_description)}</small>
                            </div>
                        ` : ''}

                        <div class="d-flex gap-2">
                            ${session.pending_approval ? `
                                <button class="btn btn-sm btn-success" onclick="approveSession(${session.id}, ${session.pending_approval.id})">
                                    <i class="bi bi-check-lg"></i> Approve
                                </button>
                                <button class="btn btn-sm btn-danger" onclick="rejectSession(${session.id}, ${session.pending_approval.id})">
                                    <i class="bi bi-x-lg"></i> Reject
                                </button>
                            ` : ''}
                            <button class="btn btn-sm btn-outline-secondary" onclick="viewSession(${session.id})">
                                <i class="bi bi-eye"></i> View
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="cancelSession(${session.id})">
                                <i class="bi bi-stop-circle"></i> Cancel
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function getStatusBadge(status) {
    switch (status) {
        case 'active':
        case 'running':
            return '<span class="badge bg-primary"><i class="bi bi-circle-fill blink me-1"></i>Active</span>';
        case 'waiting_approval':
            return '<span class="badge bg-warning text-dark"><i class="bi bi-pause-circle me-1"></i>Waiting Approval</span>';
        default:
            return `<span class="badge bg-secondary">${status}</span>`;
    }
}

function formatTimeAgo(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return date.toLocaleDateString();
}

function connectSSE() {
    const statusEl = document.getElementById('connection-status');

    eventSource = new EventSource('/api/chat/sessions/live/stream');

    eventSource.onopen = function() {
        statusEl.className = 'badge bg-success me-2';
        statusEl.innerHTML = '<i class="bi bi-wifi"></i> Connected';
    };

    eventSource.onerror = function() {
        statusEl.className = 'badge bg-danger me-2';
        statusEl.innerHTML = '<i class="bi bi-wifi-off"></i> Disconnected';

        // Reconnect after 5 seconds
        setTimeout(connectSSE, 5000);
    };

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        handleSSEEvent(data);
    };
}

function handleSSEEvent(event) {
    console.log('SSE event:', event);

    switch (event.type) {
        case 'session_started':
        case 'session_resumed':
        case 'session_cancelled':
        case 'session_completed':
        case 'session_failed':
            // Refresh the list
            loadSessions();
            break;
        case 'connected':
            console.log('SSE connected');
            break;
    }
}

async function cancelSession(sessionId) {
    if (!confirm('Are you sure you want to cancel this session?')) return;

    try {
        await api.post(`/api/chat/sessions/${sessionId}/cancel`);
        showToast('Session cancelled', 'success');
        loadSessions();
    } catch (error) {
        showToast('Failed to cancel session', 'error');
    }
}

async function approveSession(sessionId, approvalId) {
    try {
        await api.post(`/api/approvals/${approvalId}/approve`, {});
        showToast('Approval granted', 'success');
        loadSessions();
    } catch (error) {
        showToast('Failed to approve', 'error');
    }
}

async function rejectSession(sessionId, approvalId) {
    const note = prompt('Rejection reason (optional):');

    try {
        await api.post(`/api/approvals/${approvalId}/reject`, { note: note || null });
        showToast('Approval rejected', 'success');
        loadSessions();
    } catch (error) {
        showToast('Failed to reject', 'error');
    }
}

async function viewSession(sessionId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;

    // Redirect to agent chat with session
    window.location.href = `/agents/${session.agent_id}/chat?session=${sessionId}`;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (eventSource) {
        eventSource.close();
    }
});
</script>
<style>
.blink {
    animation: blink 1s ease-in-out infinite;
}
@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
.session-card {
    transition: transform 0.2s, box-shadow 0.2s;
}
.session-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}
</style>
{% endblock %}
```

**Step 2: Add route to frontend main.py**

Add after the `sessions_list` route (around line 262):

```python
@app.get("/live-sessions", response_class=HTMLResponse)
async def live_sessions(
    request: Request,
    user: ALBUser = Depends(get_current_user_optional),
):
    """Live sessions monitoring page."""
    return templates.TemplateResponse(
        "live_sessions.html",
        {
            "request": request,
            "user": user,
            "active_page": "live_sessions",
            "pending_approvals": 0,
        }
    )
```

**Step 3: Add nav link to base.html**

Add after the "Scheduled Tasks" nav item (around line 80):

```html
            <li class="nav-item">
                <a href="/live-sessions" class="nav-link {% if active_page == 'live_sessions' %}active{% endif %}">
                    <i class="fas fa-broadcast-tower"></i>
                    <span>Live Sessions</span>
                </a>
            </li>
```

**Step 4: Commit**

```bash
git add services/frontend/app/templates/live_sessions.html services/frontend/app/main.py services/frontend/app/templates/base.html
git commit -m "$(cat <<'EOF'
feat: add live sessions monitoring dashboard

- Real-time session monitoring with SSE updates
- Summary cards showing status, agent, latest message
- Quick actions: approve, reject, cancel, view
- Auto-reconnect on connection loss

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Final Integration Testing

**Step 1: Verify all files compile**

Run:
```bash
cd /home/cwdavis/scripts/netagent
python3 -m py_compile services/api/app/routes/agents.py
python3 -m py_compile services/api/app/routes/chat.py
python3 -m py_compile services/api/app/routes/approvals.py
python3 -m py_compile services/worker/app/tasks/agent_executor.py
python3 -m py_compile shared/netagent_core/redis_events.py
echo "All files compile successfully"
```

Expected: "All files compile successfully"

**Step 2: Build and test locally with docker compose**

```bash
docker compose build api worker frontend
docker compose up -d api worker frontend redis postgres
```

**Step 3: Test the live sessions endpoint**

```bash
curl http://localhost:8000/api/chat/sessions/live
```

Expected: JSON response with `items` array

**Step 4: Test session cancellation flow**

1. Create a session via the UI
2. Navigate to /live-sessions
3. Verify session appears in the grid
4. Click Cancel and verify it's removed

**Step 5: Commit final changes**

```bash
git add .
git commit -m "$(cat <<'EOF'
chore: complete long-running tasks implementation

All features implemented:
- Redis pub/sub for session events
- Worker approval polling and cancellation checks
- Live sessions dashboard with real-time updates
- Explicit tool/knowledge/MCP selection in agent UI

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Summary of Changes

### New Files
1. `shared/netagent_core/redis_events.py` - Redis pub/sub utilities
2. `services/frontend/app/templates/live_sessions.html` - Live sessions dashboard

### Modified Files
1. `shared/netagent_core/__init__.py` - Export redis_events
2. `services/api/app/routes/approvals.py` - Publish Redis events on approval
3. `services/api/app/routes/chat.py` - Cancel endpoint, live sessions SSE, remove empty=all
4. `services/api/app/routes/agents.py` - Remove ALL_TOOLS default
5. `services/worker/app/tasks/agent_executor.py` - Approval polling, cancellation checks
6. `services/frontend/app/templates/agent_create.html` - Tool/knowledge/MCP selection UI
7. `services/frontend/app/templates/base.html` - Add Live Sessions nav link
8. `services/frontend/app/main.py` - Add live sessions route
