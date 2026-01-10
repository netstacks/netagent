# Task Orchestrator Feature Design

**Date**: 2026-01-08
**Status**: Approved
**Author**: Claude + cwdavis

## Overview

The Task Orchestrator enables network engineers to submit complex, multi-step tasks via structured markdown or natural language. An orchestrator agent parses the job, spawns worker agents (pre-built or ephemeral), aggregates results, validates, delivers, and cleans up.

**Example Use Case**: "Get all Juniper MX devices from NetBox, SSH to each one, collect software version, create table, email results"

## Design Decisions

| Decision | Choice |
|----------|--------|
| Agent Model | Hybrid: Pre-built specialists + ephemeral auto-generated |
| Validation | AI sanity check (always) + Human approval (configurable per-job) |
| Execution | Batch by default (N at a time), configurable: parallel/sequential/batch |
| Job Format | Hybrid: Structured markdown OR natural language (AI generates structure for confirmation) |
| Error Handling | Continue and report by default, configurable: stop/continue/retry(N) |
| Delivery | Multiple channels: email, Slack, S3, webhook (configurable per-job) |
| Agent Selection | Smart match to existing agents first, generate ephemeral only if no match |

## Job Specification Format

### Structured Markdown

```markdown
# Job: Collect Juniper MX Software Versions

## Config
execution: batch(5)           # Run 5 devices at a time
validation: ai + human        # AI validates, then human approval
on_failure: continue          # Don't stop if some devices fail
delivery:
  - email: cwdavis@company.com
  - slack: #network-ops

## Tasks
1. **Query NetBox**
   - Get all devices where: platform=juniper, model contains "MX"
   - Agent: netbox-query (pre-built)

2. **Collect Version Info** (for each device from step 1)
   - SSH to device
   - Run: show version | match "Junos:"
   - Extract: hostname, model, software version
   - Agent: auto (generate SSH collection agent)

3. **Aggregate Results**
   - Create markdown table: hostname | model | version | status
   - Include failures section with device + error

4. **Validate**
   - Confirm all expected devices have results or documented failures
   - Check version format looks valid (XX.XRX pattern)
```

### Natural Language Alternative

User submits: "Get all Juniper MX devices from NetBox, SSH to each, get software version, create a table, email me at cwdavis@company.com"

→ Orchestrator generates structured spec and shows for confirmation before execution.

## Database Models

### Job Model (new)

```python
class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    spec_raw = Column(Text, nullable=False)           # Original input
    spec_parsed = Column(JSON)                         # Structured JSON
    status = Column(String(50), default="pending")    # See lifecycle
    execution_mode = Column(String(20), default="batch")
    batch_size = Column(Integer, default=5)
    on_failure = Column(String(20), default="continue")
    validation_mode = Column(String(20), default="ai")
    delivery_config = Column(JSON)                     # {email: [], slack: []}
    created_by = Column(Integer, ForeignKey("users.id"))
    orchestrator_session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    results = Column(JSON)                             # Aggregated results
    error_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    tasks = relationship("JobTask", back_populates="job")
    creator = relationship("User")
    orchestrator_session = relationship("AgentSession")
```

### JobTask Model (new)

```python
class JobTask(Base):
    __tablename__ = "job_tasks"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    sequence = Column(Integer, nullable=False)         # Order: 1, 2, 3...
    name = Column(String(255), nullable=False)
    spec = Column(JSON, nullable=False)                # Task details
    agent_id = Column(Integer, ForeignKey("agents.id"))  # Null if auto-gen
    agent_prompt = Column(Text)                        # Auto-generated prompt
    is_ephemeral = Column(Boolean, default=False)
    session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    status = Column(String(50), default="pending")
    result = Column(JSON)
    error = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    job = relationship("Job", back_populates="tasks")
    agent = relationship("Agent")
    session = relationship("AgentSession")
```

### AgentSession Additions

```python
# Add to existing AgentSession model:
job_id = Column(Integer, ForeignKey("jobs.id"))        # If part of a job
job_task_id = Column(Integer, ForeignKey("job_tasks.id"))  # If worker session
```

### Agent Additions

```python
# Add to existing Agent model:
is_ephemeral = Column(Boolean, default=False)
created_by_job_id = Column(Integer, ForeignKey("jobs.id"))  # If auto-generated
```

## Job Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           JOB LIFECYCLE                                  │
└─────────────────────────────────────────────────────────────────────────┘

Status Values:
  pending → awaiting_confirmation → parsing → executing →
  validating → awaiting_approval → delivering → completed
                                                    ↓
                                                  failed
                                                    ↓
                                                cancelled

1. SUBMIT (pending)
   User → POST /api/jobs with markdown spec or natural language
   → Job created with status: pending

2. PARSE (parsing / awaiting_confirmation)
   If natural language:
     → AI generates structured spec
     → Status: awaiting_confirmation
     → User confirms → continues
   If structured markdown:
     → Parse and validate spec
   → Status: parsing → executing

3. SPAWN ORCHESTRATOR
   → Create AgentSession for orchestrator agent
   → Celery task: execute_job(job_id)

4. EXECUTE TASKS (executing)
   Orchestrator manages task execution:

   For each task:
   a. Select/Create Agent (smart matching)
      - Check if task explicitly names an agent
      - Score existing agents by relevance (description, tools, history)
      - If match found: use existing agent
      - If no match: generate ephemeral agent

   b. Spawn Worker Session
      - Create AgentSession linked to job_task
      - Pass context from previous tasks

   c. Execute (respecting batch/parallel config)
      - Batch mode: run N workers concurrently
      - Track progress, handle failures per config

   d. Collect Results
      - Worker completes → results in JobTask
      - Orchestrator aggregates for next step

5. VALIDATE (validating / awaiting_approval)
   → AI validation runs (always)
   → If human approval required:
      → Status: awaiting_approval
      → Create Approval record
      → Notify user (Slack/email)
   → User approves → continues

6. DELIVER (delivering)
   → Format results per delivery config
   → Send to each channel (email, Slack, S3, etc.)
   → Status: delivering → completed

7. CLEANUP
   → Delete ephemeral agents created for this job
   → Mark worker sessions as completed
   → Keep job for audit trail
```

## Smart Agent Selection Algorithm

```
Task: "SSH to Juniper device and run 'show version'"

1. CHECK EXPLICIT ASSIGNMENT
   If task.agent specified (e.g., "netbox-query"):
   → Use that agent directly

2. SEMANTIC MATCH TO EXISTING AGENTS
   Query all agents, score by relevance:

   For each agent:
     - Does agent.description match task intent? (AI scoring)
     - Does agent have required tools? (ssh_command, netbox, etc.)
     - Has agent done similar tasks before? (history check)
     - Is agent scoped appropriately? (not too broad/narrow)

   If good match found (score > threshold):
   → Use existing agent, create new session

3. GENERATE EPHEMERAL AGENT (only if no match)
   → AI generates focused system prompt for the specific task
   → Create agent with is_ephemeral=True, created_by_job_id set
   → Agent has only the tools needed for this task
   → Agent deleted when job completes
```

## API Endpoints

### Job Management

```
POST   /api/jobs                    # Submit new job
GET    /api/jobs                    # List jobs (filters: status, created_by)
GET    /api/jobs/{job_id}           # Get job details + tasks + results
POST   /api/jobs/{job_id}/confirm   # Confirm parsed spec (natural language)
POST   /api/jobs/{job_id}/cancel    # Cancel running job
POST   /api/jobs/{job_id}/retry     # Retry failed job
DELETE /api/jobs/{job_id}           # Delete job (if not running)
```

### Job Tasks

```
GET    /api/jobs/{job_id}/tasks                # List tasks for a job
GET    /api/jobs/{job_id}/tasks/{task_id}      # Get task details + session
```

### Job Results

```
GET    /api/jobs/{job_id}/results              # Get aggregated results
POST   /api/jobs/{job_id}/redeliver            # Re-send to delivery channels
```

### Orchestrator Config

```
GET    /api/agents/orchestrator                # Get orchestrator agent config
PUT    /api/agents/orchestrator                # Update orchestrator settings
```

## UI Components

### 1. Jobs Dashboard (`/jobs`)
- List of jobs with status badges (color-coded)
- Columns: Name, Status, Tasks (completed/total), Created, Duration
- Filters: status dropdown, date range, created by
- Quick actions: View, Cancel, Retry, Delete
- Real-time status updates via SSE

### 2. Job Detail View (`/jobs/{id}`)
- Header: Job name, status badge, created by, timestamps
- Tabs:
  - **Spec**: Original input + parsed structure (side-by-side)
  - **Tasks**: List with progress bars, expand for details
  - **Results**: Formatted output, download options
  - **Logs**: Orchestrator session messages
- Live updates during execution
- Approval button if awaiting_approval

### 3. Submit Job (`/jobs/new`)
- Mode toggle: "Structured" / "Natural Language"
- Structured mode:
  - Monaco editor with markdown syntax highlighting
  - Template dropdown (saved job specs)
  - Preview parsed structure button
- Natural language mode:
  - Simple textarea
  - "Parse & Preview" button shows generated structure
- Submit → creates job, redirects to detail view

### 4. Job Confirmation Modal
- Shows AI-generated structure from natural language
- Editable fields before confirmation
- "This is what I understood. Make changes if needed."
- Confirm / Edit / Cancel buttons

### 5. Sidebar Addition
- Add "Jobs" link under Operations section
- Badge showing running job count

## Delivery Integrations

### Email
- Use existing email utility (if available) or add SendGrid/SES
- Format results as HTML table
- Attach CSV/JSON if configured

### Slack
- Post to channel or DM
- Use blocks for formatted results
- Include job link for details

### S3/File
- Upload results to S3 bucket
- Support multiple formats: JSON, CSV, Markdown
- Return presigned URL in job results

### Webhook
- POST results to configured URL
- Include job metadata
- Support custom headers

## Files to Create/Modify

### New Files
- `shared/netagent_core/db/models/job.py` - Job and JobTask models
- `services/api/app/routes/jobs.py` - Job API endpoints
- `services/worker/app/tasks/job_executor.py` - Celery job execution
- `services/worker/app/tasks/job_parser.py` - Parse job specs
- `services/worker/app/tasks/agent_matcher.py` - Smart agent selection
- `services/worker/app/tasks/delivery.py` - Result delivery handlers
- `services/frontend/app/templates/jobs.html` - Jobs dashboard
- `services/frontend/app/templates/job_detail.html` - Job detail view
- `services/frontend/app/templates/job_create.html` - Submit job page
- `static/js/jobs.js` - Jobs UI JavaScript

### Modified Files
- `shared/netagent_core/db/models/__init__.py` - Export new models
- `shared/netagent_core/db/models/session.py` - Add job_id, job_task_id
- `shared/netagent_core/db/models/agent.py` - Add is_ephemeral, created_by_job_id
- `services/api/app/main.py` - Register jobs router
- `services/frontend/app/main.py` - Add jobs routes
- `services/frontend/app/templates/base.html` - Add Jobs to sidebar

## Implementation Phases

### Phase 1: Foundation
1. Create Job and JobTask database models
2. Add migrations
3. Create basic job API endpoints (CRUD)
4. Create jobs list UI page

### Phase 2: Job Parsing
1. Implement structured markdown parser
2. Implement natural language → structure conversion
3. Add confirmation flow for NL input
4. Create job submission UI

### Phase 3: Orchestration Engine
1. Create orchestrator Celery task
2. Implement smart agent selection/matching
3. Implement ephemeral agent generation
4. Implement batch/parallel execution
5. Implement result aggregation

### Phase 4: Validation & Approval
1. Implement AI validation step
2. Integrate with existing Approval system
3. Add approval UI to job detail

### Phase 5: Delivery
1. Implement email delivery
2. Implement Slack delivery
3. Implement S3/file delivery
4. Implement webhook delivery
5. Add redeliver functionality

### Phase 6: Cleanup & Polish
1. Implement ephemeral agent cleanup
2. Add job retry functionality
3. Add live status updates (SSE)
4. Add job templates feature
5. Testing and refinement

## Success Criteria

1. User can submit a complex multi-step task via markdown or natural language
2. System correctly parses and structures the job
3. Orchestrator spawns appropriate workers (existing or ephemeral)
4. Tasks execute in configured mode (batch/parallel/sequential)
5. Results are aggregated and validated
6. Human approval works when configured
7. Results delivered to specified channels
8. Ephemeral agents cleaned up after job completion
9. Full audit trail maintained
