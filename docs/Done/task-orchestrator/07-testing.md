# Phase 7: Testing

## Task 7.1: Add Unit Tests

**Files:**
- Create: `tests/unit/test_job_parser.py`
- Create: `tests/unit/test_agent_matcher.py`
- Create: `tests/integration/test_job_api.py`

### Step 1: Test job parser

Create `tests/unit/test_job_parser.py`:

```python
"""Unit tests for job specification parser."""

import pytest
from netagent_core.job import JobSpecParser, ParsedJobSpec, ParsedTask


class TestJobSpecParser:
    """Tests for JobSpecParser."""

    def test_parse_structured_markdown_basic(self):
        """Test parsing a basic structured markdown spec."""
        spec = """
# Job: Test Job

## Config
- execution: batch(5)
- validation: ai + human

## Tasks
1. **Query Devices**
   - Get devices from NetBox
   - Agent: netbox-query
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.name == "Test Job"
        assert result.execution_mode == "batch"
        assert result.batch_size == 5
        assert result.validation_mode == "ai+human"
        assert len(result.tasks) == 1
        assert result.tasks[0].name == "Query Devices"
        assert result.tasks[0].agent_hint == "netbox-query"

    def test_parse_structured_with_delivery(self):
        """Test parsing delivery configuration."""
        spec = """
# Job: Delivery Test

## Config
- execution: sequential
- delivery:
  - email: test@example.com
  - slack: #network-ops
  - webhook: https://hooks.example.com/notify

## Tasks
1. **Task One**
   - Do something
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.execution_mode == "sequential"
        assert "test@example.com" in result.delivery.get("email", [])
        assert "#network-ops" in result.delivery.get("slack", [])
        assert "https://hooks.example.com/notify" in result.delivery.get("webhook", [])

    def test_parse_parallel_execution(self):
        """Test parsing parallel execution mode."""
        spec = """
# Job: Parallel Test

## Config
- execution: parallel

## Tasks
1. **Task One**
   - Do something
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.execution_mode == "parallel"

    def test_parse_on_failure_stop(self):
        """Test parsing on_failure: stop configuration."""
        spec = """
# Job: Fail Fast

## Config
- on_failure: stop

## Tasks
1. **Task One**
   - Do something
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.on_failure == "stop"

    def test_parse_on_failure_retry(self):
        """Test parsing on_failure: retry configuration."""
        spec = """
# Job: Retry Test

## Config
- on_failure: retry(5)

## Tasks
1. **Task One**
   - Do something
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.on_failure == "retry"
        assert result.retry_count == 5

    def test_parse_natural_language(self):
        """Test parsing natural language input."""
        spec = "Get all routers and check their status"

        parser = JobSpecParser()
        result = parser.parse(spec)

        assert "natural_language" in result.raw_config
        assert result.name == "Get all routers and check their status"
        # Natural language should use defaults
        assert result.execution_mode == "batch"
        assert result.batch_size == 5

    def test_parse_multiple_tasks(self):
        """Test parsing multiple tasks."""
        spec = """
# Job: Multi-Task

## Tasks
1. **First Task**
   - Step one
   - Agent: agent-a

2. **Second Task**
   - Step two
   - For each device from step 1

3. **Third Task**
   - Step three
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert len(result.tasks) == 3
        assert result.tasks[0].sequence == 1
        assert result.tasks[0].name == "First Task"
        assert result.tasks[1].sequence == 2
        assert result.tasks[1].is_batch == True
        assert result.tasks[2].sequence == 3

    def test_parse_batch_task_detection(self):
        """Test detection of batch tasks."""
        spec = """
# Job: Batch Test

## Tasks
1. **Get Devices**
   - Query NetBox

2. **Process Each** (for each device from step 1)
   - SSH to device
   - Run command
"""
        parser = JobSpecParser()
        result = parser.parse(spec)

        assert result.tasks[0].is_batch == False
        assert result.tasks[1].is_batch == True
        assert result.tasks[1].batch_source == "task_1"

    def test_parse_empty_spec(self):
        """Test parsing empty specification."""
        parser = JobSpecParser()
        result = parser.parse("")

        assert result.name == "Natural Language Job"
        assert "natural_language" in result.raw_config

    def test_parse_validation_modes(self):
        """Test parsing different validation modes."""
        specs = [
            ("- validation: ai", "ai"),
            ("- validation: human", "human"),
            ("- validation: ai + human", "ai+human"),
            ("- validation: AI and Human approval", "ai+human"),
        ]

        parser = JobSpecParser()

        for config_line, expected_mode in specs:
            spec = f"""
# Job: Validation Test

## Config
{config_line}

## Tasks
1. **Task**
   - Do something
"""
            result = parser.parse(spec)
            assert result.validation_mode == expected_mode, f"Failed for: {config_line}"
```

### Step 2: Test agent matcher

Create `tests/unit/test_agent_matcher.py`:

```python
"""Unit tests for agent matcher."""

import pytest
from unittest.mock import MagicMock, patch
from netagent_core.job import AgentMatcher


class TestAgentMatcher:
    """Tests for AgentMatcher."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()

    def test_find_by_explicit_hint_exact_match(self):
        """Test finding agent by exact name hint."""
        mock_agent = MagicMock()
        mock_agent.name = "netbox-query"
        mock_agent.enabled = True

        self.mock_db.query().filter().first.return_value = mock_agent

        matcher = AgentMatcher(self.mock_db)
        agent, score, reason = matcher.find_best_agent(
            task_name="Query devices",
            task_description="Get devices from NetBox",
            agent_hint="netbox-query",
        )

        assert agent == mock_agent
        assert score == 1.0
        assert "Explicit match" in reason

    def test_find_by_explicit_hint_not_found(self):
        """Test behavior when hinted agent is not found."""
        self.mock_db.query().filter().first.return_value = None
        self.mock_db.query().filter().all.return_value = []

        matcher = AgentMatcher(self.mock_db)
        agent, score, reason = matcher.find_best_agent(
            task_name="Query devices",
            task_description="Get devices from NetBox",
            agent_hint="nonexistent-agent",
        )

        assert agent is None
        assert score == 0.0

    def test_find_best_match_by_scoring(self):
        """Test finding best agent by scoring when no hint provided."""
        mock_agent1 = MagicMock()
        mock_agent1.name = "network-diagnostic"
        mock_agent1.description = "Diagnoses network issues"
        mock_agent1.agent_type = "diagnostic"
        mock_agent1.allowed_tools = ["ssh_command"]
        mock_agent1.enabled = True
        mock_agent1.is_ephemeral = False

        mock_agent2 = MagicMock()
        mock_agent2.name = "netbox-query"
        mock_agent2.description = "Queries NetBox for device inventory"
        mock_agent2.agent_type = "netbox"
        mock_agent2.allowed_tools = ["mcp_netbox"]
        mock_agent2.enabled = True
        mock_agent2.is_ephemeral = False

        self.mock_db.query().filter().first.return_value = None
        self.mock_db.query().filter().all.return_value = [mock_agent1, mock_agent2]

        matcher = AgentMatcher(self.mock_db)
        agent, score, reason = matcher.find_best_agent(
            task_name="Query NetBox",
            task_description="Get device inventory from NetBox",
        )

        # Should match netbox-query better
        assert agent == mock_agent2
        assert score > 0.4

    def test_no_agents_available(self):
        """Test behavior when no agents are available."""
        self.mock_db.query().filter().first.return_value = None
        self.mock_db.query().filter().all.return_value = []

        matcher = AgentMatcher(self.mock_db)
        agent, score, reason = matcher.find_best_agent(
            task_name="Some task",
            task_description="Do something",
        )

        assert agent is None
        assert "No agents available" in reason

    def test_auto_hint_triggers_search(self):
        """Test that 'auto' hint triggers agent search."""
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"
        mock_agent.description = "Test agent"
        mock_agent.agent_type = "network"
        mock_agent.allowed_tools = ["ssh_command"]
        mock_agent.enabled = True
        mock_agent.is_ephemeral = False

        self.mock_db.query().filter().all.return_value = [mock_agent]

        matcher = AgentMatcher(self.mock_db)
        agent, score, reason = matcher.find_best_agent(
            task_name="Network task",
            task_description="SSH to network device",
            agent_hint="auto",
        )

        # Should search instead of exact match
        assert "Explicit match" not in reason

    def test_generate_ephemeral_prompt(self):
        """Test ephemeral prompt generation."""
        matcher = AgentMatcher(self.mock_db)

        prompt = matcher.generate_ephemeral_prompt(
            task_name="Collect Versions",
            task_description="SSH to devices and run 'show version'",
        )

        assert "Collect Versions" in prompt
        assert "show version" in prompt
        assert "Focus ONLY" in prompt  # Instruction to stay focused

    def test_generate_ephemeral_prompt_with_context(self):
        """Test ephemeral prompt with job context."""
        matcher = AgentMatcher(self.mock_db)

        prompt = matcher.generate_ephemeral_prompt(
            task_name="Process Data",
            task_description="Process the collected data",
            job_context={"job_id": 123, "job_name": "Test Job"},
        )

        assert "Process Data" in prompt
        assert "Job Context" in prompt

    def test_infer_required_tools(self):
        """Test tool inference from task description."""
        matcher = AgentMatcher(self.mock_db)

        # Test SSH detection
        tools = matcher._infer_required_tools("ssh to router and run show command")
        assert "ssh_command" in tools

        # Test NetBox detection
        tools = matcher._infer_required_tools("query netbox for device inventory")
        assert "mcp_netbox" in tools

        # Test email detection
        tools = matcher._infer_required_tools("send email with results")
        assert "send_email" in tools

    def test_text_similarity(self):
        """Test text similarity calculation."""
        matcher = AgentMatcher(self.mock_db)

        # Identical texts
        score = matcher._text_similarity("network device", "network device")
        assert score == 1.0

        # Partial overlap
        score = matcher._text_similarity("network device", "network router switch")
        assert 0 < score < 1

        # No overlap
        score = matcher._text_similarity("apple", "orange")
        assert score == 0.0
```

### Step 3: Integration tests

Create `tests/integration/test_job_api.py`:

```python
"""Integration tests for job API endpoints."""

import pytest
from fastapi.testclient import TestClient


class TestJobAPI:
    """Tests for job API endpoints."""

    def test_submit_structured_job(self, client, auth_headers):
        """Test submitting a structured job."""
        spec = """
# Job: Test Job

## Config
- execution: batch(5)

## Tasks
1. **Test Task**
   - Do something
"""
        response = client.post(
            "/api/jobs/",
            json={"spec": spec},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["name"] == "Test Job"
        assert len(data["tasks"]) == 1

    def test_submit_natural_language_job(self, client, auth_headers):
        """Test submitting a natural language job."""
        response = client.post(
            "/api/jobs/",
            json={"spec": "Get all routers and check their status"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "awaiting_confirmation"

    def test_list_jobs(self, client, auth_headers):
        """Test listing jobs."""
        response = client.get("/api/jobs/", headers=auth_headers)

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_jobs_with_filter(self, client, auth_headers):
        """Test listing jobs with status filter."""
        response = client.get(
            "/api/jobs/?status=pending",
            headers=auth_headers,
        )

        assert response.status_code == 200
        jobs = response.json()
        for job in jobs:
            assert job["status"] == "pending"

    def test_get_job_detail(self, client, auth_headers, test_job):
        """Test getting job details."""
        response = client.get(
            f"/api/jobs/{test_job.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_job.id
        assert "tasks" in data

    def test_get_job_not_found(self, client, auth_headers):
        """Test getting non-existent job."""
        response = client.get(
            "/api/jobs/99999",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_start_pending_job(self, client, auth_headers, test_job):
        """Test starting a pending job."""
        response = client.post(
            f"/api/jobs/{test_job.id}/start",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"

    def test_start_non_pending_job_fails(self, client, auth_headers, running_job):
        """Test that starting a non-pending job fails."""
        response = client.post(
            f"/api/jobs/{running_job.id}/start",
            headers=auth_headers,
        )

        assert response.status_code == 400

    def test_cancel_running_job(self, client, auth_headers, running_job):
        """Test cancelling a running job."""
        response = client.post(
            f"/api/jobs/{running_job.id}/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    def test_retry_failed_job(self, client, auth_headers, failed_job):
        """Test retrying a failed job."""
        response = client.post(
            f"/api/jobs/{failed_job.id}/retry",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"

    def test_delete_completed_job(self, client, auth_headers, completed_job):
        """Test deleting a completed job."""
        response = client.delete(
            f"/api/jobs/{completed_job.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200

        # Verify deletion
        response = client.get(
            f"/api/jobs/{completed_job.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_running_job_fails(self, client, auth_headers, running_job):
        """Test that deleting a running job fails."""
        response = client.delete(
            f"/api/jobs/{running_job.id}",
            headers=auth_headers,
        )

        assert response.status_code == 400

    def test_get_job_results(self, client, auth_headers, completed_job):
        """Test getting job results."""
        response = client.get(
            f"/api/jobs/{completed_job.id}/results",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "tasks" in data

    def test_redeliver_results(self, client, auth_headers, completed_job):
        """Test redelivering job results."""
        response = client.post(
            f"/api/jobs/{completed_job.id}/redeliver",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "redelivery_queued"


# Fixtures would be defined in conftest.py
@pytest.fixture
def test_job(db, test_user):
    """Create a test job."""
    from netagent_core.db import Job, JobTask

    job = Job(
        name="Test Job",
        spec_raw="# Test",
        status="pending",
        created_by=test_user.id,
    )
    db.add(job)
    db.flush()

    task = JobTask(
        job_id=job.id,
        sequence=1,
        name="Test Task",
        spec={},
    )
    db.add(task)
    db.commit()

    return job


@pytest.fixture
def running_job(db, test_user):
    """Create a running job."""
    from netagent_core.db import Job

    job = Job(
        name="Running Job",
        spec_raw="# Test",
        status="executing",
        created_by=test_user.id,
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture
def failed_job(db, test_user):
    """Create a failed job."""
    from netagent_core.db import Job

    job = Job(
        name="Failed Job",
        spec_raw="# Test",
        status="failed",
        error_summary="Test error",
        created_by=test_user.id,
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture
def completed_job(db, test_user):
    """Create a completed job."""
    from netagent_core.db import Job

    job = Job(
        name="Completed Job",
        spec_raw="# Test",
        status="completed",
        results={"task_1": {"success": True}},
        created_by=test_user.id,
    )
    db.add(job)
    db.commit()
    return job
```

### Step 4: Run tests

```bash
# Run unit tests
pytest tests/unit/test_job_parser.py -v
pytest tests/unit/test_agent_matcher.py -v

# Run integration tests
pytest tests/integration/test_job_api.py -v

# Run all job-related tests
pytest tests/ -k "job" -v
```

### Step 5: Commit

```bash
git add tests/
git commit -m "test: add job orchestration tests"
```

---

## Verification

### 1. Run All Tests

```bash
# Unit tests
pytest tests/unit/test_job_parser.py -v
pytest tests/unit/test_agent_matcher.py -v

# Integration tests
pytest tests/integration/test_job_api.py -v

# All job tests
pytest tests/ -k "job" -v --tb=short

# With coverage
pytest tests/ -k "job" --cov=netagent_core.job --cov=services.api.app.routes.jobs --cov-report=term-missing
```

### 2. Verify Test Count

```bash
pytest tests/ -k "job" --collect-only | grep "test_"
# Expected: 30+ tests collected
```

### Expected Outcomes

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Coverage > 80% for job modules
- [ ] No flaky tests

---

## Summary

After completing all 7 phases, you will have:

1. **Database models** for jobs and tasks
2. **Parser** for structured markdown and natural language specs
3. **Agent matcher** with smart selection algorithm
4. **API endpoints** for full job lifecycle management
5. **Celery task** for background job execution
6. **Frontend UI** for job management
7. **Tests** for validation

The system reuses existing infrastructure:
- Notification tasks for delivery
- Agent executor for worker sessions
- Approval system for human validation
- Redis pub/sub for live updates
