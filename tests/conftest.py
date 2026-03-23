"""Pytest configuration and fixtures for NetAgent tests."""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Add paths for imports
sys.path.insert(0, '/app/shared')
sys.path.insert(0, '/app')

# Also add local paths for development
local_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(local_base, 'shared'))


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.all.return_value = []
    session.add.return_value = None
    session.commit.return_value = None
    session.flush.return_value = None
    session.refresh = lambda x: None
    session.delete.return_value = None
    return session


@pytest.fixture
def mock_user():
    """Create a mock ALBUser for testing."""
    user = MagicMock()
    user.email = "test@example.com"
    user.name = "Test User"
    user.sub = "test-user-123"
    return user


@pytest.fixture
def sample_job_spec_structured():
    """Sample structured job specification."""
    return """## Config
mode: batch
batch_size: 5
on_failure: continue
validation: permissive

## Tasks
### 1. Check device health
agent: network-monitor
Run health checks on all core switches to verify they are operational.

### 2. Backup configurations
agent: config-manager
Backup running configurations from all devices in the inventory.

### 3. Generate report
Compile results and generate a summary report.

## Deliver
email: admin@example.com, ops@example.com
slack: #network-alerts"""


@pytest.fixture
def sample_job_spec_natural():
    """Sample natural language job specification."""
    return """Check the health status of all network devices in the data center,
then backup their running configurations to the central repository,
and finally send a summary report to the network operations team on Slack."""


@pytest.fixture
def mock_job():
    """Create a mock Job model."""
    job = MagicMock()
    job.id = 1
    job.name = "Test Job"
    job.spec_raw = "test spec"
    job.spec_parsed = {}
    job.status = "pending"
    job.execution_mode = "batch"
    job.batch_size = 5
    job.validation_mode = "permissive"
    job.on_failure = "continue"
    job.retry_count = 0
    job.delivery_config = None
    job.total_tasks = 3
    job.completed_tasks = 0
    job.failed_tasks = 0
    job.results = None
    job.error_summary = None
    job.created_by = 1
    job.created_at = None
    job.started_at = None
    job.completed_at = None
    job.tasks = []
    return job


@pytest.fixture
def mock_job_task():
    """Create a mock JobTask model."""
    task = MagicMock()
    task.id = 1
    task.job_id = 1
    task.sequence = 1
    task.name = "Test Task"
    task.description = "A test task"
    task.spec = {}
    task.status = "pending"
    task.agent_id = None
    task.agent_name_hint = None
    task.ephemeral_agent_id = None
    task.ephemeral_prompt = None
    task.is_ephemeral_agent = False
    task.is_batch = False
    task.batch_items = None
    task.batch_results = None
    task.session_id = None
    task.result = None
    task.error = None
    task.started_at = None
    task.completed_at = None
    return task


@pytest.fixture
def mock_agent():
    """Create a mock Agent model."""
    agent = MagicMock()
    agent.id = 1
    agent.name = "test-agent"
    agent.description = "A test agent"
    agent.system_prompt = "You are a test agent."
    agent.tools = ["ssh_command", "config_backup"]
    agent.enabled = True
    agent.is_ephemeral = False
    agent.created_for_job_id = None
    return agent


# Mock Redis for tests
@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis connections for testing."""
    with patch('redis.Redis'):
        yield


# Mock Celery for tests
@pytest.fixture(autouse=True)
def mock_celery():
    """Mock Celery task dispatching for testing."""
    try:
        with patch('celery.shared_task', lambda **kwargs: lambda f: f):
            yield
    except (ImportError, ModuleNotFoundError):
        # Celery not installed in test env, skip mocking
        yield


@pytest.fixture
def mock_alert():
    """Create a mock Alert model."""
    from datetime import datetime

    alert = MagicMock()
    alert.id = 1
    alert.source_type = "syslog"
    alert.severity = "major"
    alert.alert_type = "interface_down"
    alert.title = "Interface GigabitEthernet0/1 is down"
    alert.description = "Link failure detected"
    alert.device_name = "core-rtr-01"
    alert.device_ip = "10.1.1.1"
    alert.interface_name = "GigabitEthernet0/1"
    alert.status = "new"
    alert.correlation_key = "abc123"
    alert.correlation_count = 1
    alert.triage_session_id = None
    alert.handler_session_id = None
    alert.received_at = datetime.utcnow()
    alert.occurred_at = None
    alert.resolved_at = None
    alert.raw_data = {"raw_message": "Interface GigabitEthernet0/1 is down"}
    return alert


@pytest.fixture
def sample_syslog_messages():
    """Sample syslog messages for testing normalization."""
    return [
        {
            "raw": "<131>Jan 10 08:15:23 core-rtr-01 Interface GigabitEthernet0/1 is down",
            "facility": 16,
            "severity": 3,
            "source_ip": "10.1.1.1",
            "expected_type": "interface_down",
        },
        {
            "raw": "<134>Jan 10 08:20:00 edge-sw-01 BGP peer 10.0.0.1 session reset",
            "facility": 16,
            "severity": 6,
            "source_ip": "10.1.2.1",
            "expected_type": "bgp_peer_down",
        },
        {
            "raw": "<132>Jan 10 09:00:00 core-rtr-02 CPU utilization exceeded threshold",
            "facility": 16,
            "severity": 4,
            "source_ip": "10.1.1.2",
            "expected_type": "high_cpu",
        },
    ]
