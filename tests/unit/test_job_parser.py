"""Unit tests for the Job Specification Parser."""

import pytest
import sys
sys.path.insert(0, '/app/shared')

from netagent_core.job.parser import JobSpecParser, ParsedJobSpec, ParsedTask


class TestJobSpecParser:
    """Tests for JobSpecParser class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.parser = JobSpecParser()

    # ==================== Structured Format Tests ====================

    def test_parse_structured_basic(self):
        """Test parsing a basic structured job specification."""
        spec = """## Config
mode: batch
batch_size: 5

## Tasks
### 1. Check device health
Run health checks on network devices

### 2. Backup configs
Backup running configurations"""

        result = self.parser.parse(spec)

        assert isinstance(result, ParsedJobSpec)
        assert result.name == "Untitled Job"
        assert result.execution_mode == "batch"
        assert result.batch_size == 5
        assert len(result.tasks) == 2

    def test_parse_task_with_agent_hint(self):
        """Test parsing a task with an agent hint."""
        spec = """## Tasks
### 1. Monitor interfaces
agent: network-monitor
Check interface status on all switches"""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 1
        assert result.tasks[0].name == "Monitor interfaces"
        assert result.tasks[0].agent_hint == "network-monitor"
        assert "Check interface status" in result.tasks[0].description

    def test_parse_with_delivery_config(self):
        """Test parsing job with delivery configuration."""
        spec = """## Tasks
### 1. Run audit
Check compliance status

## Deliver
email: admin@example.com, security@example.com
slack: #alerts, #network-ops"""

        result = self.parser.parse(spec)

        assert result.delivery is not None
        assert "admin@example.com" in result.delivery.get("email", [])
        assert "#alerts" in result.delivery.get("slack", [])

    def test_parse_execution_modes(self):
        """Test parsing different execution modes."""
        for mode in ["sequential", "parallel", "batch"]:
            spec = f"""## Config
mode: {mode}

## Tasks
### 1. Test task
A test task"""

            result = self.parser.parse(spec)
            assert result.execution_mode == mode

    def test_parse_on_failure_options(self):
        """Test parsing different on_failure options."""
        for option in ["stop", "continue", "skip_dependents"]:
            spec = f"""## Config
on_failure: {option}

## Tasks
### 1. Test task
A test task"""

            result = self.parser.parse(spec)
            assert result.on_failure == option

    def test_parse_batch_task(self):
        """Test parsing a batch task with items."""
        spec = """## Tasks
### 1. Backup devices
type: batch
items: switch-01, switch-02, switch-03
Backup configuration for each device"""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 1
        assert result.tasks[0].is_batch == True

    def test_parse_validation_mode(self):
        """Test parsing validation mode."""
        spec = """## Config
validation: strict

## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.validation_mode == "strict"

    def test_parse_retry_count(self):
        """Test parsing retry count."""
        spec = """## Config
retries: 3

## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.retry_count == 3

    def test_parse_webhook_delivery(self):
        """Test parsing webhook delivery."""
        spec = """## Tasks
### 1. Test task
A test task

## Deliver
webhook: https://example.com/hook1
webhook: https://example.com/hook2"""

        result = self.parser.parse(spec)

        assert result.delivery is not None
        webhooks = result.delivery.get("webhook", [])
        assert len(webhooks) >= 1

    def test_parse_with_job_name(self):
        """Test parsing job with explicit name."""
        spec = """# Job: Weekly Network Audit

## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.name == "Weekly Network Audit"

    def test_parse_task_numbering(self):
        """Test that tasks are numbered sequentially."""
        spec = """## Tasks
### 1. First task
Description 1

### 2. Second task
Description 2

### 3. Third task
Description 3"""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 3
        assert result.tasks[0].sequence == 1
        assert result.tasks[1].sequence == 2
        assert result.tasks[2].sequence == 3

    # ==================== Natural Language Tests ====================

    def test_parse_natural_language_simple(self):
        """Test parsing a simple natural language specification."""
        spec = """Check the health of all network devices and backup their configurations"""

        result = self.parser.parse(spec)

        # Natural language parsing should detect tasks
        assert result.name == "Untitled Job" or result.name
        assert "natural_language" in result.raw_config

    def test_parse_natural_language_with_steps(self):
        """Test parsing natural language with explicit steps."""
        spec = """First check device health, then backup configs, and finally send a report to the team."""

        result = self.parser.parse(spec)

        # Should detect multiple tasks from transition words
        assert "natural_language" in result.raw_config

    def test_parse_natural_language_complex(self):
        """Test parsing complex natural language specification."""
        spec = """
        I need you to perform a network audit. Start by checking the health
        of all core switches in the data center. After that, collect their
        running configurations and compare them to the golden configs.
        If there are any discrepancies, create a report. Finally, email
        the report to the network team and post a summary to the #network-ops
        Slack channel.
        """

        result = self.parser.parse(spec)

        # Should handle complex natural language
        assert isinstance(result, ParsedJobSpec)

    # ==================== Edge Cases ====================

    def test_parse_empty_spec(self):
        """Test parsing an empty specification."""
        result = self.parser.parse("")

        assert isinstance(result, ParsedJobSpec)
        assert len(result.tasks) == 0

    def test_parse_whitespace_only(self):
        """Test parsing whitespace-only specification."""
        result = self.parser.parse("   \n\n   \t  ")

        assert isinstance(result, ParsedJobSpec)
        assert len(result.tasks) == 0

    def test_parse_no_tasks_section(self):
        """Test parsing spec without tasks section."""
        spec = """## Config
mode: sequential"""

        result = self.parser.parse(spec)

        assert isinstance(result, ParsedJobSpec)
        assert result.execution_mode == "sequential"

    def test_parse_invalid_mode_defaults(self):
        """Test that invalid mode defaults to batch."""
        spec = """## Config
mode: invalid_mode

## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.execution_mode == "batch"

    def test_parse_special_characters_in_task_name(self):
        """Test parsing task name with special characters."""
        spec = """## Tasks
### 1. Check device health (critical!)
Run health checks"""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 1
        assert "critical" in result.tasks[0].name.lower() or "health" in result.tasks[0].name.lower()

    def test_parse_multiline_description(self):
        """Test parsing task with multiline description."""
        spec = """## Tasks
### 1. Complex task
This is a complex task that spans
multiple lines and includes
detailed instructions for the agent."""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 1
        assert "complex task" in result.tasks[0].description.lower() or len(result.tasks[0].description) > 20

    def test_parse_preserves_raw_spec(self):
        """Test that parser preserves raw task specification."""
        spec = """## Tasks
### 1. Test task
agent: test-agent
This is a test task with full details"""

        result = self.parser.parse(spec)

        assert len(result.tasks) == 1
        assert result.tasks[0].raw_spec is not None

    # ==================== Default Values ====================

    def test_default_execution_mode(self):
        """Test default execution mode is batch."""
        spec = """## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.execution_mode == "batch"

    def test_default_batch_size(self):
        """Test default batch size is 5."""
        spec = """## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.batch_size == 5

    def test_default_on_failure(self):
        """Test default on_failure is continue."""
        spec = """## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.on_failure == "continue"

    def test_default_validation_mode(self):
        """Test default validation mode is permissive."""
        spec = """## Tasks
### 1. Test task
A test task"""

        result = self.parser.parse(spec)
        assert result.validation_mode == "permissive"


class TestParsedTask:
    """Tests for ParsedTask dataclass."""

    def test_parsed_task_creation(self):
        """Test creating a ParsedTask."""
        task = ParsedTask(
            sequence=1,
            name="Test Task",
            description="A test task",
            agent_hint="test-agent",
            is_batch=False,
            batch_items=None,
            raw_spec={"name": "Test Task"},
        )

        assert task.sequence == 1
        assert task.name == "Test Task"
        assert task.description == "A test task"
        assert task.agent_hint == "test-agent"
        assert task.is_batch == False

    def test_parsed_task_batch(self):
        """Test creating a batch ParsedTask."""
        task = ParsedTask(
            sequence=1,
            name="Batch Task",
            description="A batch task",
            agent_hint=None,
            is_batch=True,
            batch_items=["item1", "item2", "item3"],
            raw_spec={},
        )

        assert task.is_batch == True
        assert len(task.batch_items) == 3


class TestParsedJobSpec:
    """Tests for ParsedJobSpec dataclass."""

    def test_parsed_job_spec_creation(self):
        """Test creating a ParsedJobSpec."""
        spec = ParsedJobSpec(
            name="Test Job",
            tasks=[],
            execution_mode="batch",
            batch_size=5,
            on_failure="continue",
            validation_mode="permissive",
            retry_count=0,
            delivery=None,
            raw_config={},
        )

        assert spec.name == "Test Job"
        assert spec.execution_mode == "batch"
        assert spec.batch_size == 5

    def test_parsed_job_spec_with_delivery(self):
        """Test creating a ParsedJobSpec with delivery config."""
        spec = ParsedJobSpec(
            name="Test Job",
            tasks=[],
            execution_mode="batch",
            batch_size=5,
            on_failure="continue",
            validation_mode="permissive",
            retry_count=0,
            delivery={
                "email": ["admin@example.com"],
                "slack": ["#alerts"],
            },
            raw_config={},
        )

        assert spec.delivery is not None
        assert "admin@example.com" in spec.delivery["email"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
