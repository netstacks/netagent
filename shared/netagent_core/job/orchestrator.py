"""Job orchestrator - coordinates multi-task job execution.

The orchestrator is responsible for:
1. Building a dependency graph from task specifications
2. Executing tasks in correct order (respecting dependencies)
3. Extracting outputs from completed tasks
4. Passing relevant data to dependent tasks
5. Handling failures and deciding whether to continue

This module implements an agent-driven orchestration pattern where
the orchestrator understands the full job context and coordinates
sub-agents to complete individual tasks.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any, Optional, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    """A task in the dependency graph."""

    task_id: int
    sequence: int
    name: str
    description: str
    agent_hint: Optional[str] = None
    depends_on: List[int] = field(default_factory=list)  # List of task sequences this depends on
    is_batch: bool = False
    batch_source: Optional[int] = None  # Sequence number of task to get batch items from

    # Execution state
    status: str = "pending"  # pending, ready, running, completed, failed, skipped
    result: Optional[Dict[str, Any]] = None
    output_data: Optional[Any] = None  # Extracted output for dependent tasks
    error: Optional[str] = None


class DependencyGraph:
    """Manages task dependencies and execution order."""

    def __init__(self):
        self.nodes: Dict[int, TaskNode] = {}  # sequence -> TaskNode
        self.edges: Dict[int, Set[int]] = defaultdict(set)  # sequence -> set of dependent sequences
        self.reverse_edges: Dict[int, Set[int]] = defaultdict(set)  # sequence -> set of dependencies

    def add_task(self, node: TaskNode) -> None:
        """Add a task to the graph."""
        self.nodes[node.sequence] = node

        # Add edges for explicit dependencies
        for dep_seq in node.depends_on:
            self.edges[dep_seq].add(node.sequence)
            self.reverse_edges[node.sequence].add(dep_seq)

        # Batch tasks depend on their source
        if node.batch_source:
            self.edges[node.batch_source].add(node.sequence)
            self.reverse_edges[node.sequence].add(node.batch_source)

    def get_ready_tasks(self) -> List[TaskNode]:
        """Get tasks that are ready to execute (all dependencies satisfied)."""
        ready = []

        for seq, node in self.nodes.items():
            if node.status != "pending":
                continue

            # Check if all dependencies are completed
            deps = self.reverse_edges.get(seq, set())
            all_deps_satisfied = all(
                self.nodes[dep_seq].status == "completed"
                for dep_seq in deps
            )

            if all_deps_satisfied:
                ready.append(node)

        return ready

    def get_dependency_outputs(self, task_seq: int) -> Dict[str, Any]:
        """Get outputs from all dependencies of a task."""
        outputs = {}

        deps = self.reverse_edges.get(task_seq, set())
        for dep_seq in deps:
            dep_node = self.nodes.get(dep_seq)
            if dep_node and dep_node.output_data is not None:
                outputs[f"task_{dep_seq}"] = dep_node.output_data
                outputs[dep_node.name] = dep_node.output_data  # Also key by name

        return outputs

    def mark_completed(self, task_seq: int, result: Dict[str, Any], output_data: Any = None) -> None:
        """Mark a task as completed with its result."""
        node = self.nodes.get(task_seq)
        if node:
            node.status = "completed"
            node.result = result
            node.output_data = output_data

    def mark_failed(self, task_seq: int, error: str) -> None:
        """Mark a task as failed."""
        node = self.nodes.get(task_seq)
        if node:
            node.status = "failed"
            node.error = error

    def mark_skipped(self, task_seq: int, reason: str = "Dependency failed") -> None:
        """Mark a task as skipped (due to dependency failure)."""
        node = self.nodes.get(task_seq)
        if node:
            node.status = "skipped"
            node.error = reason

    def skip_dependents(self, failed_seq: int) -> List[int]:
        """Skip all tasks that depend on a failed task. Returns skipped sequences."""
        skipped = []
        to_check = list(self.edges.get(failed_seq, set()))

        while to_check:
            seq = to_check.pop(0)
            node = self.nodes.get(seq)
            if node and node.status == "pending":
                self.mark_skipped(seq, f"Dependency task_{failed_seq} failed")
                skipped.append(seq)
                # Also skip anything that depends on this
                to_check.extend(self.edges.get(seq, set()))

        return skipped

    def is_complete(self) -> bool:
        """Check if all tasks are in a terminal state."""
        return all(
            node.status in ("completed", "failed", "skipped")
            for node in self.nodes.values()
        )

    def get_summary(self) -> Dict[str, int]:
        """Get execution summary."""
        summary = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
        for node in self.nodes.values():
            if node.status in summary:
                summary[node.status] += 1
        return summary


class OutputExtractor:
    """Extracts structured data from agent task outputs.

    This class analyzes agent responses and extracts data that can be
    passed to subsequent tasks. It handles various output formats:
    - Lists of devices/items
    - Tables of data
    - Key-value pairs
    - Structured JSON
    """

    def extract(self, task_name: str, response: str, tool_results: List[Dict] = None) -> Any:
        """Extract usable output data from an agent response.

        Args:
            task_name: Name of the task (for context)
            response: The agent's text response
            tool_results: Any tool call results from the agent execution

        Returns:
            Extracted data (could be list, dict, string, etc.)
        """
        # If we have tool results, prioritize those as they're structured
        if tool_results:
            extracted = self._extract_from_tool_results(tool_results)
            if extracted:
                return extracted

        # Try to extract structured data from the response text
        return self._extract_from_text(response)

    def _extract_from_tool_results(self, tool_results: List[Dict]) -> Any:
        """Extract data from tool results."""
        # Collect all meaningful results
        extracted = []

        for result in tool_results:
            tool_name = result.get("name", "")
            output = result.get("result", {})

            # NetBox query results - extract the actual data
            if "netbox" in tool_name.lower():
                if isinstance(output, dict) and "results" in output:
                    extracted.extend(output["results"])
                elif isinstance(output, list):
                    extracted.extend(output)

            # SSH command results
            elif "ssh" in tool_name.lower():
                if isinstance(output, str):
                    extracted.append({"command_output": output})
                elif isinstance(output, dict):
                    extracted.append(output)

            # Generic structured output
            elif isinstance(output, (list, dict)):
                if isinstance(output, list):
                    extracted.extend(output)
                else:
                    extracted.append(output)

        return extracted if extracted else None

    def _extract_from_text(self, response: str) -> Any:
        """Try to extract structured data from text response."""
        import json
        import re

        if not response:
            return None

        # Try to find JSON in the response
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to parse as JSON directly (sometimes the whole response is JSON)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Look for bullet lists of items
        list_items = re.findall(r'^[\s]*[-*]\s+(.+)$', response, re.MULTILINE)
        if list_items:
            return list_items

        # Look for numbered lists
        numbered_items = re.findall(r'^\s*\d+[.)]\s+(.+)$', response, re.MULTILINE)
        if numbered_items:
            return numbered_items

        # Look for device names/hostnames (common pattern)
        hostnames = re.findall(r'\b([a-zA-Z0-9]+-[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9.-]+)?)\b', response)
        if len(hostnames) >= 3:  # If we found multiple hostnames, it's probably a list
            return list(set(hostnames))

        # Return the text as-is if no structure found
        return response[:5000]  # Limit size


class JobOrchestrator:
    """Orchestrates multi-task job execution.

    The orchestrator manages the execution flow of a job:
    1. Builds dependency graph from tasks
    2. Determines execution order
    3. Executes tasks in order, passing results between them
    4. Handles failures according to job configuration
    """

    def __init__(self, job_id: int, db_session):
        """Initialize orchestrator for a job.

        Args:
            job_id: ID of the job to orchestrate
            db_session: Database session
        """
        self.job_id = job_id
        self.db = db_session
        self.graph = DependencyGraph()
        self.extractor = OutputExtractor()
        self.on_failure = "continue"  # stop, continue, retry

    def initialize(self) -> bool:
        """Load job and build dependency graph.

        Returns:
            True if initialization successful
        """
        from ..db import Job, JobTask

        self.job = self.db.query(Job).filter(Job.id == self.job_id).first()
        if not self.job:
            logger.error(f"Job {self.job_id} not found")
            return False

        self.on_failure = self.job.on_failure

        # Load tasks and build graph
        tasks = self.db.query(JobTask).filter(
            JobTask.job_id == self.job_id
        ).order_by(JobTask.sequence).all()

        for task in tasks:
            # Parse dependencies from task spec
            spec = task.spec or {}
            depends_on = []

            # Check for explicit depends_on in spec
            if "depends_on" in spec:
                deps = spec["depends_on"]
                if isinstance(deps, list):
                    depends_on = deps
                elif isinstance(deps, int):
                    depends_on = [deps]

            # For sequential jobs, each task depends on the previous
            if self.job.execution_mode == "sequential" and task.sequence > 1:
                depends_on.append(task.sequence - 1)

            # Check for batch source reference
            batch_source = None
            if task.is_batch and spec.get("batch_source"):
                # batch_source format: "task_1" -> extract 1
                batch_ref = spec["batch_source"]
                if batch_ref.startswith("task_"):
                    try:
                        batch_source = int(batch_ref.split("_")[1])
                    except (ValueError, IndexError):
                        pass

            node = TaskNode(
                task_id=task.id,
                sequence=task.sequence,
                name=task.name,
                description=task.description or "",
                agent_hint=task.agent_name_hint,
                depends_on=depends_on,
                is_batch=task.is_batch,
                batch_source=batch_source,
            )
            self.graph.add_task(node)

        logger.info(f"Job {self.job_id}: Built dependency graph with {len(self.graph.nodes)} tasks")
        return True

    def get_next_tasks(self) -> List[TaskNode]:
        """Get the next tasks that are ready to execute."""
        return self.graph.get_ready_tasks()

    def build_task_context(self, task: TaskNode) -> Dict[str, Any]:
        """Build execution context for a task including data from dependencies.

        Args:
            task: The task to build context for

        Returns:
            Context dict with dependency outputs and batch items
        """
        context = {
            "job_id": self.job_id,
            "job_name": self.job.name,
            "task_sequence": task.sequence,
            "task_name": task.name,
        }

        # Add outputs from dependencies
        dep_outputs = self.graph.get_dependency_outputs(task.sequence)
        if dep_outputs:
            context["previous_results"] = dep_outputs

        # For batch tasks, extract batch items from source
        if task.is_batch and task.batch_source:
            source_node = self.graph.nodes.get(task.batch_source)
            if source_node and source_node.output_data:
                context["batch_items"] = source_node.output_data

        return context

    def build_task_prompt(self, task: TaskNode, context: Dict[str, Any]) -> str:
        """Build the prompt to send to the task agent.

        This creates a detailed prompt that includes:
        - Task description
        - Data from previous tasks
        - Instructions for output format

        Args:
            task: The task to build prompt for
            context: Context from build_task_context

        Returns:
            Prompt string
        """
        parts = [
            f"# Task: {task.name}",
            "",
        ]

        if task.description:
            parts.extend([
                "## Objective",
                task.description,
                "",
            ])

        # Add previous task results if any
        prev_results = context.get("previous_results")
        if prev_results:
            parts.extend([
                "## Input Data from Previous Tasks",
                "Use this data to complete your task:",
                "",
            ])

            for source_name, data in prev_results.items():
                if source_name.startswith("task_"):
                    parts.append(f"### From {source_name}:")
                    if isinstance(data, list):
                        # Format list data nicely
                        if len(data) <= 20:
                            for item in data:
                                if isinstance(item, dict):
                                    parts.append(f"- {item}")
                                else:
                                    parts.append(f"- {item}")
                        else:
                            parts.append(f"({len(data)} items)")
                            for item in data[:10]:
                                parts.append(f"- {item}")
                            parts.append(f"... and {len(data) - 10} more items")
                    elif isinstance(data, dict):
                        import json
                        parts.append("```json")
                        parts.append(json.dumps(data, indent=2)[:2000])
                        parts.append("```")
                    else:
                        parts.append(str(data)[:2000])
                    parts.append("")

        # For batch tasks, include the batch items
        batch_items = context.get("batch_items")
        if batch_items:
            parts.extend([
                "## Items to Process",
                f"Process each of these {len(batch_items)} items:",
                "",
            ])
            if isinstance(batch_items, list):
                for item in batch_items[:50]:  # Limit for prompt size
                    parts.append(f"- {item}")
                if len(batch_items) > 50:
                    parts.append(f"... and {len(batch_items) - 50} more")
            parts.append("")

        # Instructions for output
        parts.extend([
            "## Instructions",
            "1. Complete the task using the available tools",
            "2. Be thorough but efficient",
            "3. Report your findings clearly",
            "",
            "If you produce a list of items, data, or structured information,",
            "format it clearly so it can be used by subsequent tasks.",
        ])

        return "\n".join(parts)

    def complete_task(
        self,
        task_seq: int,
        result: Dict[str, Any],
        tool_results: List[Dict] = None
    ) -> None:
        """Mark a task as completed and extract its output.

        Args:
            task_seq: Sequence number of completed task
            result: Result dict from agent execution
            tool_results: Tool results from agent execution
        """
        from ..db import JobTask

        task_node = self.graph.nodes.get(task_seq)
        if not task_node:
            return

        # Extract output data for dependent tasks
        response = result.get("response") or result.get("output", {}).get("response", "")
        output_data = self.extractor.extract(
            task_node.name,
            response,
            tool_results
        )

        self.graph.mark_completed(task_seq, result, output_data)

        # Update database
        db_task = self.db.query(JobTask).filter(JobTask.id == task_node.task_id).first()
        if db_task:
            db_task.status = "completed"
            db_task.result = {
                "response": response[:10000],  # Limit stored response
                "output_data": output_data if not isinstance(output_data, str) else None,
                "tool_calls": result.get("tool_calls", 0),
            }
            db_task.completed_at = datetime.utcnow()
            self.db.commit()

        logger.info(f"Task {task_seq} completed, extracted output: {type(output_data)}")

    def fail_task(self, task_seq: int, error: str) -> List[int]:
        """Mark a task as failed and handle dependents.

        Args:
            task_seq: Sequence number of failed task
            error: Error message

        Returns:
            List of skipped task sequences (if on_failure is "stop")
        """
        from ..db import JobTask

        self.graph.mark_failed(task_seq, error)

        # Update database
        task_node = self.graph.nodes.get(task_seq)
        if task_node:
            db_task = self.db.query(JobTask).filter(JobTask.id == task_node.task_id).first()
            if db_task:
                db_task.status = "failed"
                db_task.error = error
                db_task.completed_at = datetime.utcnow()
                self.db.commit()

        skipped = []
        if self.on_failure == "stop":
            # Skip all dependent tasks
            skipped = self.graph.skip_dependents(task_seq)
            for seq in skipped:
                node = self.graph.nodes.get(seq)
                if node:
                    db_task = self.db.query(JobTask).filter(JobTask.id == node.task_id).first()
                    if db_task:
                        db_task.status = "skipped"
                        db_task.error = f"Dependency task_{task_seq} failed"
                        self.db.commit()

        return skipped

    def is_complete(self) -> bool:
        """Check if job execution is complete."""
        return self.graph.is_complete()

    def get_status(self) -> Dict[str, Any]:
        """Get current orchestration status."""
        summary = self.graph.get_summary()
        return {
            "job_id": self.job_id,
            "total_tasks": len(self.graph.nodes),
            "completed": summary["completed"],
            "failed": summary["failed"],
            "skipped": summary["skipped"],
            "pending": summary["pending"],
            "running": summary["running"],
            "is_complete": self.is_complete(),
        }
