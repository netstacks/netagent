# Phase 2: Job Parser

## Task 2.1: Create Job Specification Parser

**Files:**
- Create: `shared/netagent_core/job/parser.py`
- Create: `shared/netagent_core/job/__init__.py`

### Step 1: Create the job module init

Create `shared/netagent_core/job/__init__.py`:

```python
"""Job orchestration module."""

from .parser import JobSpecParser, ParsedJobSpec, ParsedTask

__all__ = ["JobSpecParser", "ParsedJobSpec", "ParsedTask"]
```

### Step 2: Create the parser

Create `shared/netagent_core/job/parser.py`:

```python
"""Job specification parser.

Parses structured markdown or natural language job specs into executable format.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedTask:
    """A parsed task from the job spec."""
    sequence: int
    name: str
    description: str = ""
    agent_hint: Optional[str] = None  # e.g., "netbox-query" or "auto"
    is_batch: bool = False
    batch_source: Optional[str] = None  # Reference to previous task output
    commands: list = field(default_factory=list)
    extract_fields: list = field(default_factory=list)
    raw_spec: dict = field(default_factory=dict)


@dataclass
class ParsedJobSpec:
    """A fully parsed job specification."""
    name: str
    execution_mode: str = "batch"
    batch_size: int = 5
    on_failure: str = "continue"
    retry_count: int = 3
    validation_mode: str = "ai"
    delivery: dict = field(default_factory=dict)
    tasks: list = field(default_factory=list)
    raw_config: dict = field(default_factory=dict)


class JobSpecParser:
    """Parses job specifications from markdown or natural language."""

    # Regex patterns for structured markdown
    HEADER_PATTERN = re.compile(r"^#\s+(?:Job:\s*)?(.+)$", re.MULTILINE)
    CONFIG_SECTION = re.compile(r"##\s*Config\s*\n([\s\S]*?)(?=##|\Z)", re.IGNORECASE)
    TASKS_SECTION = re.compile(r"##\s*Tasks\s*\n([\s\S]*?)(?=##|\Z)", re.IGNORECASE)
    TASK_PATTERN = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*\s*(.*?)(?=^\d+\.|\Z)", re.MULTILINE | re.DOTALL)
    CONFIG_LINE = re.compile(r"^[-*]?\s*(\w+):\s*(.+)$", re.MULTILINE)

    def parse(self, spec_raw: str) -> ParsedJobSpec:
        """Parse a job specification.

        Args:
            spec_raw: Raw markdown or natural language spec

        Returns:
            ParsedJobSpec with all configuration and tasks
        """
        if self._is_structured_markdown(spec_raw):
            return self._parse_structured(spec_raw)
        else:
            return self._parse_natural_language(spec_raw)

    def _is_structured_markdown(self, spec: str) -> bool:
        """Check if spec is structured markdown format."""
        has_header = bool(self.HEADER_PATTERN.search(spec))
        has_config = bool(self.CONFIG_SECTION.search(spec))
        has_tasks = bool(self.TASKS_SECTION.search(spec))
        return has_header and (has_config or has_tasks)

    def _parse_structured(self, spec: str) -> ParsedJobSpec:
        """Parse structured markdown format."""
        header_match = self.HEADER_PATTERN.search(spec)
        name = header_match.group(1).strip() if header_match else "Untitled Job"

        result = ParsedJobSpec(name=name)

        # Parse config section
        config_match = self.CONFIG_SECTION.search(spec)
        if config_match:
            config_text = config_match.group(1)
            result.raw_config = self._parse_config(config_text)

            # Apply config values
            if "execution" in result.raw_config:
                exec_val = result.raw_config["execution"]
                if "batch" in exec_val:
                    result.execution_mode = "batch"
                    batch_match = re.search(r"batch\((\d+)\)", exec_val)
                    if batch_match:
                        result.batch_size = int(batch_match.group(1))
                elif "parallel" in exec_val:
                    result.execution_mode = "parallel"
                elif "sequential" in exec_val:
                    result.execution_mode = "sequential"

            if "validation" in result.raw_config:
                val = result.raw_config["validation"].lower()
                if "human" in val and "ai" in val:
                    result.validation_mode = "ai+human"
                elif "human" in val:
                    result.validation_mode = "human"
                else:
                    result.validation_mode = "ai"

            if "on_failure" in result.raw_config:
                fail_val = result.raw_config["on_failure"].lower()
                if "stop" in fail_val:
                    result.on_failure = "stop"
                elif "retry" in fail_val:
                    result.on_failure = "retry"
                    retry_match = re.search(r"retry\((\d+)\)", fail_val)
                    if retry_match:
                        result.retry_count = int(retry_match.group(1))
                else:
                    result.on_failure = "continue"

            if "delivery" in result.raw_config:
                result.delivery = self._parse_delivery(result.raw_config["delivery"])

        # Parse tasks section
        tasks_match = self.TASKS_SECTION.search(spec)
        if tasks_match:
            tasks_text = tasks_match.group(1)
            result.tasks = self._parse_tasks(tasks_text)

        return result

    def _parse_config(self, config_text: str) -> dict:
        """Parse configuration key-value pairs."""
        config = {}
        current_key = None
        current_value = []

        for line in config_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            match = self.CONFIG_LINE.match(line)
            if match:
                if current_key:
                    config[current_key] = "\n".join(current_value).strip()
                current_key = match.group(1).lower()
                current_value = [match.group(2)]
            elif current_key and line.startswith("-"):
                current_value.append(line)

        if current_key:
            config[current_key] = "\n".join(current_value).strip()

        return config

    def _parse_delivery(self, delivery_text: str) -> dict:
        """Parse delivery configuration."""
        delivery = {"email": [], "slack": [], "s3": [], "webhook": []}

        for line in delivery_text.split("\n"):
            line = line.strip().lstrip("-").strip()
            if not line:
                continue

            if line.startswith("email:"):
                delivery["email"].append(line.replace("email:", "").strip())
            elif line.startswith("slack:"):
                delivery["slack"].append(line.replace("slack:", "").strip())
            elif line.startswith("s3:"):
                delivery["s3"].append(line.replace("s3:", "").strip())
            elif line.startswith("webhook:"):
                delivery["webhook"].append(line.replace("webhook:", "").strip())

        return delivery

    def _parse_tasks(self, tasks_text: str) -> list:
        """Parse task definitions."""
        tasks = []
        task_blocks = re.split(r"(?=^\d+\.)", tasks_text, flags=re.MULTILINE)

        for i, block in enumerate(task_blocks):
            block = block.strip()
            if not block:
                continue

            lines = block.split("\n")
            first_line = lines[0] if lines else ""

            name_match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*", first_line)
            if name_match:
                name = name_match.group(1)
            else:
                name_match = re.match(r"^\d+\.\s+(.+?)(?:\s*\(|$)", first_line)
                name = name_match.group(1) if name_match else f"Task {i+1}"

            task = ParsedTask(
                sequence=len(tasks) + 1,
                name=name.strip(),
                raw_spec={"raw_text": block}
            )

            if "for each" in block.lower():
                task.is_batch = True
                source_match = re.search(r"from step (\d+)", block.lower())
                if source_match:
                    task.batch_source = f"task_{source_match.group(1)}"

            agent_match = re.search(r"Agent:\s*(\S+)", block, re.IGNORECASE)
            if agent_match:
                task.agent_hint = agent_match.group(1).strip()

            description_lines = []
            for line in lines[1:]:
                line = line.strip().lstrip("-").strip()
                if line:
                    description_lines.append(line)
            task.description = "\n".join(description_lines)

            tasks.append(task)

        return tasks

    def _parse_natural_language(self, spec: str) -> ParsedJobSpec:
        """Create minimal spec from natural language."""
        first_sentence = spec.split(".")[0].strip()
        if len(first_sentence) > 50:
            first_sentence = first_sentence[:47] + "..."

        return ParsedJobSpec(
            name=first_sentence or "Natural Language Job",
            raw_config={"natural_language": spec}
        )
```

### Step 3: Commit

```bash
git add shared/netagent_core/job/
git commit -m "feat(job): add job specification parser"
```

---

## Verification

### 1. Test Parser

```bash
python3 -c "
from netagent_core.job import JobSpecParser

# Structured markdown
spec = '''
# Job: Test Job
## Config
- execution: batch(10)
- validation: ai + human
## Tasks
1. **Query Devices**
   - Agent: netbox-query
2. **Collect Data** (for each device from step 1)
   - SSH and run commands
'''
result = JobSpecParser().parse(spec)
assert result.name == 'Test Job'
assert result.batch_size == 10
assert result.validation_mode == 'ai+human'
assert len(result.tasks) == 2
assert result.tasks[1].is_batch == True
print('✓ Structured parsing works')

# Natural language
result = JobSpecParser().parse('Check all routers')
assert 'natural_language' in result.raw_config
print('✓ Natural language parsing works')
"
```

### Expected Outcomes

- [ ] Parses job name, config, and tasks from markdown
- [ ] Detects batch tasks ("for each" pattern)
- [ ] Falls back to natural language mode when not structured
