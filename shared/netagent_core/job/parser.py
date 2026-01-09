"""Job specification parser for structured markdown and natural language."""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedTask:
    """A parsed task from the job specification."""

    sequence: int
    name: str
    description: str = ""
    agent_hint: Optional[str] = None
    is_batch: bool = False
    batch_source: Optional[str] = None  # e.g., "task_1" meaning output from task 1
    depends_on: list = field(default_factory=list)  # List of task sequences this depends on
    raw_spec: dict = field(default_factory=dict)


@dataclass
class ParsedJobSpec:
    """Parsed job specification result."""

    name: str
    execution_mode: str = "batch"  # parallel, sequential, batch
    batch_size: int = 5
    validation_mode: str = "ai"  # ai, human, ai+human
    on_failure: str = "continue"  # stop, continue, retry
    retry_count: int = 3
    delivery: dict = field(default_factory=dict)  # {email: [], slack: [], s3: [], webhook: []}
    tasks: list = field(default_factory=list)  # List of ParsedTask
    raw_config: dict = field(default_factory=dict)


class JobSpecParser:
    """Parses job specifications from structured markdown or natural language.

    Structured markdown format:
    ```
    # Job: Job Name

    ## Config
    - execution: batch(5)
    - validation: ai + human
    - on_failure: continue
    - delivery:
      - email: user@example.com
      - slack: #channel

    ## Tasks
    1. **Task Name**
       - Description line 1
       - Agent: agent-name

    2. **Another Task** (for each device from step 1)
       - Description
    ```
    """

    # Regex patterns for parsing
    JOB_NAME_PATTERN = re.compile(r"^#\s+Job:\s*(.+)$", re.MULTILINE)
    # Section patterns - use (?=\n##[^#]|\Z) to stop at next level-2 heading but not level-3
    CONFIG_SECTION_PATTERN = re.compile(r"##\s*Config\s*\n(.*?)(?=\n##[^#]|\Z)", re.DOTALL | re.IGNORECASE)
    TASKS_SECTION_PATTERN = re.compile(r"##\s*Tasks?\s*\n(.*?)(?=\n##[^#]|\Z)", re.DOTALL | re.IGNORECASE)
    DELIVER_SECTION_PATTERN = re.compile(r"##\s*Deliver(?:y)?\s*\n(.*?)(?=\n##[^#]|\Z)", re.DOTALL | re.IGNORECASE)

    # Task patterns - supports both formats:
    # Format 1: 1. **Task Name** (with bullet descriptions)
    # Format 2: ### 1. Task Name (with inline content)
    TASK_PATTERN = re.compile(
        r"(\d+)\.\s+\*\*(.+?)\*\*\s*(\([^)]+\))?\s*\n((?:[ \t]+-[^\n]+\n?)*)",
        re.MULTILINE
    )
    # Alternative format: ### 1. Task Name (used in simpler specs)
    ALT_TASK_PATTERN = re.compile(
        r"###\s*(\d+)\.\s*(.+?)\s*\n((?:(?!###)[^\n]+\n?)*)",
        re.MULTILINE
    )

    # Config patterns - support both "- key: value" and "key: value" formats
    EXECUTION_PATTERN = re.compile(r"(?:-\s*)?execution:\s*(parallel|sequential|batch\((\d+)\))", re.IGNORECASE)
    MODE_PATTERN = re.compile(r"(?:-\s*)?mode:\s*(parallel|sequential|batch)", re.IGNORECASE)
    BATCH_SIZE_PATTERN = re.compile(r"(?:-\s*)?batch_size:\s*(\d+)", re.IGNORECASE)
    VALIDATION_PATTERN = re.compile(r"(?:-\s*)?validation:\s*(.+)", re.IGNORECASE)
    ON_FAILURE_PATTERN = re.compile(r"(?:-\s*)?on_failure:\s*(stop|continue|retry\((\d+)\))", re.IGNORECASE)
    DELIVERY_PATTERN = re.compile(r"(?:-\s*)?delivery:\s*\n((?:[ \t]+-[^\n]+\n?)*)", re.IGNORECASE)

    # Batch task detection
    BATCH_PATTERN = re.compile(r"\(for each.*?(?:from|in)\s+(?:step\s+)?(\d+)\)", re.IGNORECASE)

    # Agent hint pattern
    AGENT_PATTERN = re.compile(r"-\s*Agent:\s*(\S+)", re.IGNORECASE)

    def parse(self, spec: str) -> ParsedJobSpec:
        """Parse a job specification.

        Args:
            spec: The job specification string (markdown or natural language)

        Returns:
            ParsedJobSpec with parsed configuration and tasks
        """
        spec = spec.strip()

        # Check if it looks like structured markdown
        if self._is_structured_markdown(spec):
            return self._parse_structured(spec)
        else:
            return self._parse_natural_language(spec)

    def _is_structured_markdown(self, spec: str) -> bool:
        """Check if the spec looks like structured markdown."""
        # Look for job header or tasks section
        has_job_header = bool(self.JOB_NAME_PATTERN.search(spec))
        has_tasks_section = "## Tasks" in spec or "## Task" in spec
        has_numbered_tasks = bool(re.search(r"\d+\.\s+\*\*", spec))
        has_alt_numbered_tasks = bool(re.search(r"###\s*\d+\.", spec))
        has_config_section = "## Config" in spec

        return has_job_header or has_tasks_section or has_numbered_tasks or has_alt_numbered_tasks or has_config_section

    def _parse_structured(self, spec: str) -> ParsedJobSpec:
        """Parse structured markdown specification."""
        result = ParsedJobSpec(name="Untitled Job", raw_config={"structured": True})

        # Extract job name
        name_match = self.JOB_NAME_PATTERN.search(spec)
        if name_match:
            result.name = name_match.group(1).strip()

        # Extract config section
        config_match = self.CONFIG_SECTION_PATTERN.search(spec)
        if config_match:
            config_text = config_match.group(1)
            self._parse_config(config_text, result)

        # Extract tasks section
        tasks_match = self.TASKS_SECTION_PATTERN.search(spec)
        if tasks_match:
            tasks_text = tasks_match.group(1)
            result.tasks = self._parse_tasks(tasks_text)
            result.raw_config["tasks_raw"] = tasks_text

        # Extract delivery section (## Deliver or ## Delivery)
        deliver_match = self.DELIVER_SECTION_PATTERN.search(spec)
        if deliver_match:
            deliver_text = deliver_match.group(1)
            result.delivery = self._parse_delivery_simple(deliver_text)

        return result

    def _parse_config(self, config_text: str, result: ParsedJobSpec) -> None:
        """Parse configuration section."""
        # Execution mode - try "execution: batch(5)" format first
        exec_match = self.EXECUTION_PATTERN.search(config_text)
        if exec_match:
            mode = exec_match.group(1).lower()
            if mode.startswith("batch"):
                result.execution_mode = "batch"
                if exec_match.group(2):
                    result.batch_size = int(exec_match.group(2))
            else:
                result.execution_mode = mode
        else:
            # Try simpler "mode: batch" format
            mode_match = self.MODE_PATTERN.search(config_text)
            if mode_match:
                result.execution_mode = mode_match.group(1).lower()

        # Batch size (separate field in simple format)
        batch_match = self.BATCH_SIZE_PATTERN.search(config_text)
        if batch_match:
            result.batch_size = int(batch_match.group(1))

        # Validation mode
        val_match = self.VALIDATION_PATTERN.search(config_text)
        if val_match:
            val_text = val_match.group(1).lower().strip()
            # Normalize validation mode
            if "human" in val_text and "ai" in val_text:
                result.validation_mode = "ai+human"
            elif "human" in val_text:
                result.validation_mode = "human"
            else:
                result.validation_mode = "ai"

        # On failure behavior
        failure_match = self.ON_FAILURE_PATTERN.search(config_text)
        if failure_match:
            mode = failure_match.group(1).lower()
            if mode.startswith("retry"):
                result.on_failure = "retry"
                if failure_match.group(2):
                    result.retry_count = int(failure_match.group(2))
            else:
                result.on_failure = mode

        # Delivery configuration
        delivery_match = self.DELIVERY_PATTERN.search(config_text)
        if delivery_match:
            result.delivery = self._parse_delivery(delivery_match.group(1))

    def _parse_delivery(self, delivery_text: str) -> dict:
        """Parse delivery configuration."""
        delivery = {"email": [], "slack": [], "s3": [], "webhook": []}

        for line in delivery_text.strip().split("\n"):
            line = line.strip()
            if not line.startswith("-"):
                continue

            line = line[1:].strip()  # Remove leading dash

            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                if key == "email":
                    delivery["email"].append(value)
                elif key == "slack":
                    delivery["slack"].append(value)
                elif key == "s3":
                    delivery["s3"].append(value)
                elif key == "webhook":
                    delivery["webhook"].append(value)

        return delivery

    def _parse_delivery_simple(self, deliver_text: str) -> dict:
        """Parse simple delivery section (email: x, slack: y format)."""
        delivery = {"email": [], "slack": [], "s3": [], "webhook": []}

        for line in deliver_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Remove optional leading dash
            if line.startswith("-"):
                line = line[1:].strip()

            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                if value:
                    if key == "email":
                        delivery["email"].append(value)
                    elif key == "slack":
                        delivery["slack"].append(value)
                    elif key == "s3":
                        delivery["s3"].append(value)
                    elif key == "webhook":
                        delivery["webhook"].append(value)

        return delivery

    def _parse_tasks(self, tasks_text: str) -> list:
        """Parse tasks from the tasks section."""
        tasks = []

        # Patterns for dependencies
        depends_pattern = re.compile(r"depends(?:_on)?:\s*(.+)", re.IGNORECASE)
        uses_pattern = re.compile(r"uses(?:\s+output\s+from)?:\s*(.+)", re.IGNORECASE)

        # Try primary format: 1. **Task Name**
        for match in self.TASK_PATTERN.finditer(tasks_text):
            sequence = int(match.group(1))
            name = match.group(2).strip()
            modifier = match.group(3) or ""
            body = match.group(4) or ""

            task = ParsedTask(
                sequence=sequence,
                name=name,
                raw_spec={"modifier": modifier, "body": body}
            )

            # Check if this is a batch task (implies dependency on source)
            batch_match = self.BATCH_PATTERN.search(modifier)
            if batch_match:
                task.is_batch = True
                source_seq = int(batch_match.group(1))
                task.batch_source = f"task_{source_seq}"
                task.depends_on.append(source_seq)

            # Check modifier for "uses output from step X" pattern
            uses_match = re.search(r"uses?\s+(?:output\s+)?from\s+(?:step\s+)?(\d+)", modifier, re.IGNORECASE)
            if uses_match:
                dep_seq = int(uses_match.group(1))
                if dep_seq not in task.depends_on:
                    task.depends_on.append(dep_seq)

            # Parse body for description, agent hint, and dependencies
            description_lines = []
            for line in body.strip().split("\n"):
                line = line.strip()
                if not line.startswith("-"):
                    continue

                line_content = line[1:].strip()

                # Check for agent hint
                agent_match = self.AGENT_PATTERN.match("-" + line_content)
                if agent_match:
                    task.agent_hint = agent_match.group(1)
                    continue

                # Check for explicit depends_on
                dep_match = depends_pattern.match(line_content)
                if dep_match:
                    deps_str = dep_match.group(1)
                    # Parse "1, 2" or "task_1, task_2" or "step 1, step 2"
                    for dep in re.findall(r'\d+', deps_str):
                        dep_seq = int(dep)
                        if dep_seq not in task.depends_on:
                            task.depends_on.append(dep_seq)
                    continue

                # Check for "uses output from" pattern
                uses_match = uses_pattern.match(line_content)
                if uses_match:
                    deps_str = uses_match.group(1)
                    for dep in re.findall(r'\d+', deps_str):
                        dep_seq = int(dep)
                        if dep_seq not in task.depends_on:
                            task.depends_on.append(dep_seq)
                    continue

                description_lines.append(line_content)

            task.description = "\n".join(description_lines)
            tasks.append(task)

        # If no tasks found, try alternative format: ### 1. Task Name
        if not tasks:
            tasks = self._parse_tasks_alt(tasks_text)

        return tasks

    def _parse_tasks_alt(self, tasks_text: str) -> list:
        """Parse tasks using alternative format (### 1. Task Name)."""
        tasks = []

        for match in self.ALT_TASK_PATTERN.finditer(tasks_text):
            sequence = int(match.group(1))
            name = match.group(2).strip()
            body = match.group(3) or ""

            task = ParsedTask(
                sequence=sequence,
                name=name,
                raw_spec={"body": body}
            )

            # Parse body for description and agent hint
            description_lines = []
            for line in body.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Check for agent hint (agent: name format)
                agent_match = re.match(r"agent:\s*(\S+)", line, re.IGNORECASE)
                if agent_match:
                    task.agent_hint = agent_match.group(1)
                else:
                    description_lines.append(line)

            task.description = "\n".join(description_lines)
            tasks.append(task)

        return tasks

    def _parse_natural_language(self, spec: str) -> ParsedJobSpec:
        """Parse natural language specification.

        Natural language specs are stored as-is and will be processed
        by an LLM to generate the structured format for confirmation.
        """
        # Use the first sentence or line as the name
        first_line = spec.split("\n")[0].strip()
        if len(first_line) > 100:
            # Truncate long first lines
            name = first_line[:97] + "..."
        else:
            name = first_line or "Natural Language Job"

        return ParsedJobSpec(
            name=name,
            raw_config={"natural_language": spec}
        )


def parse_job_spec(spec: str) -> ParsedJobSpec:
    """Convenience function to parse a job specification.

    Args:
        spec: The job specification string

    Returns:
        ParsedJobSpec with parsed configuration and tasks
    """
    parser = JobSpecParser()
    return parser.parse(spec)


class NaturalLanguageConverter:
    """Converts natural language job descriptions to structured specs using LLM."""

    SYSTEM_PROMPT = """You are a job specification parser. Convert natural language task descriptions into structured job specifications.

Output format (JSON):
{
    "name": "Short descriptive job name",
    "tasks": [
        {
            "sequence": 1,
            "name": "Task name",
            "description": "What this task should do",
            "agent_hint": "optional-agent-name or null",
            "depends_on": [],
            "is_batch": false,
            "batch_source": null
        },
        {
            "sequence": 2,
            "name": "Process results from task 1",
            "description": "Use the output from task 1 to do X",
            "agent_hint": null,
            "depends_on": [1],
            "is_batch": false,
            "batch_source": null
        }
    ],
    "delivery": {
        "email": ["email@example.com"],
        "slack": ["#channel-name"],
        "webhook": []
    }
}

Rules:
1. Break down the request into discrete, actionable tasks
2. Number tasks in logical execution order (1, 2, 3...)
3. Use clear, concise task names (2-5 words)
4. Include detailed descriptions of what each task should accomplish
5. CRITICAL: Identify dependencies between tasks:
   - If task B needs results from task A, set depends_on: [A's sequence number]
   - Example: If task 2 needs device list from task 1, set task 2's depends_on: [1]
   - A task can depend on multiple tasks: depends_on: [1, 2]
6. For batch operations (process each item from a list):
   - Set is_batch: true
   - Set batch_source: "task_N" where N is the task that produces the list
   - batch_source automatically adds a dependency
7. If the user mentions emailing results, add to delivery.email
8. If the user mentions Slack, add to delivery.slack
9. agent_hint should suggest which type of agent or null if unsure:
   - "netbox-query" for NetBox/inventory lookups
   - "ssh-collector" for device commands via SSH
   - "config-backup" for saving configurations
   - "network-diagnostic" for troubleshooting

Example flow for "Get all routers from NetBox, then collect their versions":
- Task 1: Query NetBox for routers (no dependencies)
- Task 2: Collect software versions (depends_on: [1], uses output from task 1)
- Task 3: Compile results (depends_on: [2], uses output from task 2)

Return ONLY valid JSON, no explanation."""

    def __init__(self, gemini_client=None):
        """Initialize converter.

        Args:
            gemini_client: Optional GeminiClient instance. If not provided,
                          one will be created when needed.
        """
        self._client = gemini_client

    def _get_client(self):
        """Get or create Gemini client."""
        if self._client is None:
            from netagent_core.llm import GeminiClient
            self._client = GeminiClient()
        return self._client

    def convert(self, natural_language: str) -> ParsedJobSpec:
        """Convert natural language to structured job spec using LLM.

        Args:
            natural_language: The natural language job description

        Returns:
            ParsedJobSpec with tasks extracted from the natural language
        """
        import json

        client = self._get_client()

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"Convert this to a job specification:\n\n{natural_language}"}
        ]

        try:
            response = client.chat(
                messages=messages,
                temperature=0.2,
            )

            # Parse the JSON response
            content = response.content.strip()
            # Handle markdown code blocks
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            data = json.loads(content)

            # Build ParsedJobSpec from LLM response
            result = ParsedJobSpec(
                name=data.get("name", "Untitled Job"),
                raw_config={"natural_language": natural_language, "llm_parsed": True}
            )

            # Parse tasks
            for task_data in data.get("tasks", []):
                task = ParsedTask(
                    sequence=task_data.get("sequence", len(result.tasks) + 1),
                    name=task_data.get("name", "Unnamed Task"),
                    description=task_data.get("description", ""),
                    agent_hint=task_data.get("agent_hint"),
                    is_batch=task_data.get("is_batch", False),
                    batch_source=task_data.get("batch_source"),
                    depends_on=task_data.get("depends_on", []),
                )
                result.tasks.append(task)

            # Parse delivery
            if "delivery" in data:
                result.delivery = {
                    "email": data["delivery"].get("email", []),
                    "slack": data["delivery"].get("slack", []),
                    "s3": data["delivery"].get("s3", []),
                    "webhook": data["delivery"].get("webhook", []),
                }

            return result

        except Exception as e:
            logger.error(f"Failed to convert natural language: {e}")
            # Return basic spec with the natural language stored
            return ParsedJobSpec(
                name=natural_language[:50] + "..." if len(natural_language) > 50 else natural_language,
                raw_config={"natural_language": natural_language, "parse_error": str(e)}
            )

