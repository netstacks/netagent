# Phase 1: Database Models

## Task 1.1: Create Job and JobTask Models

**Files:**
- Create: `shared/netagent_core/db/models/job.py`
- Modify: `shared/netagent_core/db/models.py` (add imports)
- Modify: `shared/netagent_core/db/__init__.py` (export models)

### Step 1: Create the job models file

Create `shared/netagent_core/db/models/job.py`:

```python
"""Job orchestration models."""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base


class Job(Base):
    """Orchestrated job containing multiple tasks."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)

    # Job specification
    spec_raw = Column(Text, nullable=False)  # Original markdown/natural language
    spec_parsed = Column(JSONB)  # Structured JSON after parsing

    # Execution configuration
    status = Column(String(30), default="pending", index=True)
    execution_mode = Column(String(20), default="batch")  # parallel, sequential, batch
    batch_size = Column(Integer, default=5)
    on_failure = Column(String(20), default="continue")  # stop, continue, retry
    retry_count = Column(Integer, default=3)  # For retry mode
    validation_mode = Column(String(20), default="ai")  # ai, human, ai+human

    # Delivery configuration
    delivery_config = Column(JSONB)  # {email: [], slack: [], s3: [], webhook: []}

    # Execution tracking
    orchestrator_session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    results = Column(JSONB)  # Aggregated results from all tasks
    error_summary = Column(Text)

    # Progress tracking
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)

    # Ownership
    created_by = Column(Integer, ForeignKey("users.id"), index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    tasks = relationship("JobTask", back_populates="job", cascade="all, delete-orphan")
    creator = relationship("User")
    orchestrator_session = relationship("AgentSession", foreign_keys=[orchestrator_session_id])
    approvals = relationship("Approval", back_populates="job")

    __table_args__ = (
        Index("idx_jobs_status_created", "status", "created_at"),
    )


class JobTask(Base):
    """Individual task within a job."""

    __tablename__ = "job_tasks"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    # Task definition
    sequence = Column(Integer, nullable=False)  # Execution order: 1, 2, 3...
    name = Column(String(255), nullable=False)
    description = Column(Text)
    spec = Column(JSONB, nullable=False)  # Task details from parsed spec

    # Agent assignment
    agent_id = Column(Integer, ForeignKey("agents.id"))  # Pre-existing agent
    agent_name_hint = Column(String(100))  # Hint for agent matching (e.g., "netbox-query")
    is_ephemeral_agent = Column(Boolean, default=False)
    ephemeral_agent_id = Column(Integer, ForeignKey("agents.id"))  # Auto-generated agent
    ephemeral_prompt = Column(Text)  # Generated prompt for ephemeral agent

    # Execution
    session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    status = Column(String(30), default="pending", index=True)

    # For batch tasks (e.g., "for each device")
    is_batch = Column(Boolean, default=False)
    batch_items = Column(JSONB)  # List of items to process
    batch_results = Column(JSONB)  # Results per item

    # Results
    result = Column(JSONB)
    error = Column(Text)

    # Timestamps
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    job = relationship("Job", back_populates="tasks")
    agent = relationship("Agent", foreign_keys=[agent_id])
    ephemeral_agent = relationship("Agent", foreign_keys=[ephemeral_agent_id])
    session = relationship("AgentSession")
```

### Step 2: Update models.py to import job models

Add to end of `shared/netagent_core/db/models.py`:

```python
# Job orchestration models
from .models.job import Job, JobTask
```

### Step 3: Update __init__.py exports

Add to `shared/netagent_core/db/__init__.py`:

```python
from .models import Job, JobTask
```

### Step 4: Add job_id to Approval model

Modify `shared/netagent_core/db/models.py` - add to Approval class after `workflow_run_id`:

```python
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
```

And add relationship:

```python
    job = relationship("Job", back_populates="approvals")
```

### Step 5: Add is_ephemeral to Agent model

Modify `shared/netagent_core/db/models.py` - add to Agent class after `is_template`:

```python
    is_ephemeral = Column(Boolean, default=False)
    created_for_job_id = Column(Integer, ForeignKey("jobs.id"))
```

### Step 6: Commit

```bash
git add shared/netagent_core/db/
git commit -m "feat(db): add Job and JobTask models for orchestration"
```

---

## Task 1.2: Create Database Migration

**Files:**
- Create: `migrations/versions/xxx_add_job_orchestration.py` (Alembic generates)

### Step 1: Generate migration

```bash
cd /home/cwdavis/scripts/netagent
alembic revision --autogenerate -m "add job orchestration tables"
```

### Step 2: Review and run migration

```bash
alembic upgrade head
```

### Step 3: Commit

```bash
git add migrations/
git commit -m "feat(db): add migration for job orchestration"
```

---

## Verification

### 1. Verify Tables Created

```bash
psql $DATABASE_URL -c "\d jobs"
psql $DATABASE_URL -c "\d job_tasks"
```

### 2. Test Model CRUD

```bash
python3 -c "
from netagent_core.db import get_db_context, Job, JobTask

with get_db_context() as db:
    job = Job(name='Test', spec_raw='# Test', status='pending', created_by=1)
    db.add(job)
    db.flush()
    task = JobTask(job_id=job.id, sequence=1, name='Task 1', spec={})
    db.add(task)
    db.commit()
    assert len(job.tasks) == 1
    print(f'✓ Created job {job.id} with task')
    db.delete(job)
    db.commit()
    print('✓ Cascade delete works')
"
```

### Expected Outcomes

- [ ] `jobs` and `job_tasks` tables exist
- [ ] Foreign keys link correctly
- [ ] Cascade delete removes tasks when job deleted
