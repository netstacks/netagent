# Long-Running Tasks & Session Management Design

**Date:** 2026-01-08
**Status:** Approved

## Overview

This design addresses the management of long-running agent sessions, including:
- End-to-end approval workflows with pause/resume
- Live session monitoring dashboard
- Session cancellation capability
- Explicit tool/knowledge selection per agent (reverting ALL_TOOLS default)

## Design Decisions

| Question | Choice |
|----------|--------|
| Approval Resume Mechanism | Redis Pub/Sub + Worker Polling |
| Background Execution Model | All Background Tasks via Celery |
| Live Sessions View | Summary Cards with Click-to-Expand |
| Tool/Knowledge Selection | Explicit Checkboxes + Multi-select |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Frontend (Flask)                               │
├─────────────────────────────────────────────────────────────────────────┤
│  Live Sessions Dashboard          │  Agent Create/Edit                   │
│  - Summary cards (status, agent)  │  - Tool checkboxes (explicit)        │
│  - Click-to-expand details        │  - Knowledge multi-select            │
│  - Cancel button per session      │  - MCP server multi-select           │
│  - SSE stream for live updates    │                                      │
└─────────────────────────────────────────────────────────────────────────┘
                    │                              │
                    ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           API Service (FastAPI)                          │
├─────────────────────────────────────────────────────────────────────────┤
│  /api/sessions/live          - SSE stream for session events            │
│  /api/sessions/{id}/cancel   - Cancel running session                   │
│  /api/approvals/{id}/approve - Approve + publish Redis event            │
│  /api/approvals/{id}/reject  - Reject + publish Redis event             │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Redis                                          │
├─────────────────────────────────────────────────────────────────────────┤
│  Pub/Sub Channels:                                                       │
│  - session:{id}:events      → approval_resolved, cancelled, progress     │
│  - sessions:live            → global session events for dashboard        │
│                                                                          │
│  Keys:                                                                   │
│  - session:{id}:cancel_flag → signals worker to stop                    │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Celery Worker                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  execute_agent_session task:                                             │
│  - Runs agent executor in background                                     │
│  - On approval request: polls DB every 5s for resolution                │
│  - On cancel: checks Redis flag, raises TaskCancelled                   │
│  - Publishes progress events to Redis                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### 1. Approval Wait/Resume Flow

When an agent calls `request_approval`, the Celery worker enters a polling loop:

```python
async def handle_approval_request(session_id, approval_id):
    """Block execution until approval is resolved."""

    while True:
        # Check for cancellation first
        if redis.get(f"session:{session_id}:cancel_flag"):
            raise TaskCancelled("Session cancelled by user")

        # Poll approval status from database
        approval = db.query(Approval).filter(Approval.id == approval_id).first()

        if approval.status == "approved":
            return True  # Continue execution
        elif approval.status == "rejected":
            raise ApprovalRejected(approval.resolution_note)
        elif approval.status == "expired":
            raise ApprovalExpired()

        # Still pending - wait and poll again
        await asyncio.sleep(5)  # 5 second polling interval
```

The API approval endpoint publishes to Redis when resolved:

```python
redis.publish(f"session:{approval.session_id}:events", json.dumps({
    "type": "approval_resolved",
    "approval_id": approval.id,
    "status": "approved"
}))
```

### 2. Session Cancellation

**API Endpoint:**
```python
@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: int, db: Session, user: ALBUser):
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()

    if session.status not in ["active", "waiting_approval"]:
        raise HTTPException(400, "Session not cancellable")

    # Set cancel flag in Redis
    redis.set(f"session:{session_id}:cancel_flag", "1", ex=3600)

    # Update session status
    session.status = "cancelled"
    session.ended_at = datetime.utcnow()
    db.commit()

    # Publish event for frontend
    redis.publish("sessions:live", json.dumps({
        "type": "session_cancelled",
        "session_id": session_id
    }))

    return {"message": "Session cancelled"}
```

**Worker Check:**
```python
# Check at start of each tool execution
if redis.get(f"session:{session_id}:cancel_flag"):
    raise TaskCancelled("Cancelled by user")
```

### 3. Live Sessions Dashboard

**Summary Card Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ ● Active    Agent: Network Troubleshooter              │
│ Started: 2 minutes ago                                  │
│ "Checking interface status on core-rtr-01..."          │
│                                        [Cancel] [View] │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ ⏸ Waiting Approval    Agent: Config Deployer           │
│ Started: 5 minutes ago                                  │
│ "Requesting approval: Apply BGP config to edge-01"     │
│                              [Approve] [Reject] [View] │
└─────────────────────────────────────────────────────────┘
```

**SSE Stream:**
- Endpoint: `/api/sessions/live/stream`
- Subscribes to `sessions:live` Redis channel
- Events: `session_started`, `session_progress`, `session_completed`, `approval_requested`, `session_cancelled`

### 4. Tool/Knowledge Selection UI

**Tools Section (Checkboxes):**
```
Available Tools
┌─────────────────────────────────────────────────────────┐
│ ☑ netbox_search      Search NetBox for devices/IPs     │
│ ☑ ssh_command        Execute commands on devices       │
│ ☐ request_approval   Request human approval            │
│ ☐ handoff           Transfer to another agent          │
│ ☑ web_search        Search the web                     │
└─────────────────────────────────────────────────────────┘
```

**Knowledge Bases (Multi-select dropdown):**
```
Knowledge Bases                                    [▼]
┌─────────────────────────────────────────────────────────┐
│ ☑ Network Standards                                     │
│ ☑ Troubleshooting Runbooks                             │
│ ☐ Security Policies                                     │
└─────────────────────────────────────────────────────────┘
Selected: 2 knowledge bases
```

**MCP Servers (Multi-select dropdown):**
```
MCP Servers                                        [▼]
┌─────────────────────────────────────────────────────────┐
│ ☑ NetBox MCP                                            │
│ ☐ GitHub MCP                                            │
└─────────────────────────────────────────────────────────┘
Selected: 1 MCP server
```

## Implementation Tasks

### Phase 1: Core Infrastructure
1. Add Redis pub/sub utilities to netagent_core
2. Modify agent_executor to support approval polling
3. Add cancellation check points in agent execution loop
4. Update Celery worker with progress event publishing

### Phase 2: API Endpoints
5. Add `/api/sessions/{id}/cancel` endpoint
6. Add `/api/sessions/live/stream` SSE endpoint
7. Modify approval endpoints to publish Redis events
8. Add session progress tracking to chat endpoints

### Phase 3: Frontend - Live Sessions
9. Create live_sessions.html template
10. Implement summary card components
11. Add SSE connection for real-time updates
12. Implement click-to-expand session details
13. Add cancel/approve/reject actions

### Phase 4: Frontend - Tool Selection
14. Revert ALL_TOOLS default in agents.py
15. Add tool checkboxes to agent_create.html
16. Add knowledge base multi-select
17. Add MCP server multi-select
18. Remove "empty = all" logic from chat.py

### Phase 5: Navigation & Polish
19. Add Live Sessions link to navbar
20. Add route to frontend main.py
21. Update base.html pending approvals badge
22. Test end-to-end flows

## Files to Modify

### New Files
- `services/frontend/app/templates/live_sessions.html`
- `shared/netagent_core/redis_events.py`

### Modified Files
- `shared/netagent_core/tools/approval_tool.py` - Add approval wait logic
- `services/worker/app/tasks/agent_executor.py` - Add polling, cancellation checks
- `services/api/app/routes/approvals.py` - Add Redis publish on resolve
- `services/api/app/routes/chat.py` - Add SSE stream, remove empty=all logic
- `services/api/app/routes/agents.py` - Revert ALL_TOOLS default
- `services/frontend/app/main.py` - Add live sessions route
- `services/frontend/app/templates/agent_create.html` - Add tool/knowledge selection
- `services/frontend/app/templates/base.html` - Add nav link

## Success Criteria

1. Agent can request approval and execution pauses until resolved
2. User can approve/reject from dashboard with immediate effect
3. User can cancel any running session
4. Live sessions page shows real-time status updates
5. Tool/knowledge selection is explicit - no "all by default"
6. Session progress visible in live dashboard
