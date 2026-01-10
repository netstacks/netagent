"""Smart agent matching for job tasks."""

import logging
import re
from typing import Optional, Tuple
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Tool keywords for inference
TOOL_KEYWORDS = {
    "ssh_command": ["ssh", "telnet", "connect", "login", "show", "command", "run", "execute", "device", "router", "switch", "juniper", "cisco", "arista"],
    "mcp_netbox": ["netbox", "dcim", "ipam", "device", "inventory", "site", "rack", "vlan", "prefix"],
    "mcp_jira": ["jira", "ticket", "issue", "task"],
    "mcp_github": ["github", "repo", "repository", "pull request", "pr", "commit"],
    "search_knowledge": ["knowledge", "documentation", "docs", "wiki", "confluence", "search", "find"],
    "send_email": ["email", "mail", "send", "notify", "notification", "report"],
    "handoff": ["handoff", "delegate", "transfer", "pass"],
}

# MCP server name patterns - map MCP server names to tool categories
MCP_SERVER_TOOL_MAPPING = {
    "netbox": "mcp_netbox",
    "jira": "mcp_jira",
    "atlassian": "mcp_jira",
    "github": "mcp_github",
    "netdisco": "mcp_netdisco",
}


class AgentMatcher:
    """Matches tasks to the best available agent.

    Supports:
    - Explicit agent hints (exact name match)
    - Smart matching based on task description
    - Ephemeral agent generation when no good match exists
    """

    def __init__(self, db: Session):
        self.db = db

    def find_best_agent(
        self,
        task_name: str,
        task_description: str,
        agent_hint: Optional[str] = None,
    ) -> Tuple[Optional[object], float, str]:
        """Find the best agent for a task.

        Args:
            task_name: Name of the task
            task_description: Description of what the task does
            agent_hint: Optional hint for agent name (e.g., "netbox-query")

        Returns:
            Tuple of (agent, score, reason)
            - agent: The matched Agent object or None
            - score: Match confidence 0.0-1.0
            - reason: Human-readable explanation of the match
        """
        # Import here to avoid circular imports
        from netagent_core.db import Agent

        # 1. Try explicit hint first (if not "auto")
        if agent_hint and agent_hint.lower() != "auto":
            agent = self.db.query(Agent).filter(
                Agent.name == agent_hint,
                Agent.enabled == True,
            ).first()

            if agent:
                return (agent, 1.0, f"Explicit match by name: {agent_hint}")

            # Try partial match
            agent = self.db.query(Agent).filter(
                Agent.name.ilike(f"%{agent_hint}%"),
                Agent.enabled == True,
            ).first()

            if agent:
                return (agent, 0.9, f"Partial match by name: {agent.name}")

            logger.warning(f"Agent hint '{agent_hint}' not found, falling back to search")

        # 2. Score all available agents
        agents = self.db.query(Agent).filter(
            Agent.enabled == True,
            Agent.is_ephemeral == False,  # Don't match ephemeral agents
        ).all()

        if not agents:
            return (None, 0.0, "No agents available")

        # Infer required tools from task
        required_tools = self._infer_required_tools(
            f"{task_name} {task_description}".lower()
        )

        best_agent = None
        best_score = 0.0
        best_reason = ""

        for agent in agents:
            score, reason = self._score_agent(
                agent, task_name, task_description, required_tools
            )

            if score > best_score:
                best_score = score
                best_agent = agent
                best_reason = reason

        if best_agent and best_score >= 0.4:
            return (best_agent, best_score, best_reason)

        return (None, best_score, "No suitable agent found")

    def _score_agent(
        self,
        agent,
        task_name: str,
        task_description: str,
        required_tools: set,
    ) -> Tuple[float, str]:
        """Score how well an agent matches a task.

        Returns:
            Tuple of (score, reason)
        """
        scores = []
        reasons = []

        task_text = f"{task_name} {task_description}".lower()

        # 1. Name similarity (20% weight)
        if agent.name:
            name_sim = self._text_similarity(task_text, agent.name.lower())
            scores.append(name_sim * 0.2)
            if name_sim > 0.3:
                reasons.append(f"name match ({name_sim:.0%})")

        # 2. Description similarity (30% weight)
        if agent.description:
            desc_sim = self._text_similarity(task_text, agent.description.lower())
            scores.append(desc_sim * 0.3)
            if desc_sim > 0.3:
                reasons.append(f"description match ({desc_sim:.0%})")

        # 3. Agent type match (20% weight)
        if agent.agent_type:
            type_keywords = {
                "network": ["network", "router", "switch", "device", "ssh", "config"],
                "netbox": ["netbox", "inventory", "dcim", "ipam", "device"],
                "diagnostic": ["diagnose", "troubleshoot", "debug", "check", "verify"],
                "config": ["configure", "config", "change", "modify", "update"],
                "reporting": ["report", "summary", "collect", "gather", "audit"],
            }

            type_score = 0.0
            for agent_type, keywords in type_keywords.items():
                if agent.agent_type.lower() == agent_type:
                    matches = sum(1 for kw in keywords if kw in task_text)
                    if matches > 0:
                        type_score = min(1.0, matches / 3)
                        reasons.append(f"type '{agent_type}' matches keywords")
                        break

            scores.append(type_score * 0.2)

        # 4. Tool coverage (30% weight for regular tools, bonus for MCP tools)
        # Combine allowed_tools with MCP server capabilities
        agent_tools = set(agent.allowed_tools or [])
        agent_mcp_tools = set()

        # Add MCP capabilities based on configured MCP servers
        if agent.mcp_server_ids:
            agent_mcp_tools = self._get_mcp_tools_for_agent(agent.mcp_server_ids)
            agent_tools.update(agent_mcp_tools)

        # Separate required tools into MCP and non-MCP
        required_mcp_tools = {t for t in required_tools if t.startswith("mcp_")}
        required_regular_tools = required_tools - required_mcp_tools

        if required_tools and agent_tools:
            covered = len(required_tools & agent_tools)
            total = len(required_tools)

            if total > 0:
                tool_score = covered / total
                scores.append(tool_score * 0.3)
                if covered > 0:
                    reasons.append(f"has {covered}/{total} required tools")

                # CRITICAL: Bonus for having required MCP tools (very important for NetBox, etc.)
                if required_mcp_tools:
                    mcp_covered = len(required_mcp_tools & agent_mcp_tools)
                    if mcp_covered == len(required_mcp_tools):
                        # Agent has ALL required MCP tools - big bonus
                        scores.append(0.25)
                        reasons.append(f"has all {mcp_covered} required MCP tools")
                    elif mcp_covered > 0:
                        # Agent has SOME required MCP tools - small bonus
                        scores.append(0.1)
                        reasons.append(f"has {mcp_covered}/{len(required_mcp_tools)} MCP tools")
                    else:
                        # Agent is MISSING required MCP tools - penalty
                        scores.append(-0.2)
                        reasons.append(f"missing required MCP tools: {required_mcp_tools}")
            else:
                scores.append(0.15)  # Default if no tools inferred
        else:
            scores.append(0.15)  # Default

        total_score = sum(scores)
        reason = "; ".join(reasons) if reasons else "low match"

        return (total_score, reason)

    def _get_mcp_tools_for_agent(self, mcp_server_ids: list) -> set:
        """Get MCP tool capabilities for an agent based on configured servers.

        Args:
            mcp_server_ids: List of MCP server IDs

        Returns:
            Set of MCP tool category names
        """
        from netagent_core.db import MCPServer

        tools = set()

        servers = self.db.query(MCPServer).filter(
            MCPServer.id.in_(mcp_server_ids),
            MCPServer.enabled == True,
        ).all()

        for server in servers:
            server_name_lower = server.name.lower()
            for pattern, tool_name in MCP_SERVER_TOOL_MAPPING.items():
                if pattern in server_name_lower:
                    tools.add(tool_name)
                    break

        return tools

    def _get_mcp_server_ids_for_tools(self, required_tools: set) -> list:
        """Get MCP server IDs that provide the required tool capabilities.

        Args:
            required_tools: Set of required tool names (e.g., {"mcp_netbox", "mcp_jira"})

        Returns:
            List of MCP server IDs that provide those capabilities
        """
        from netagent_core.db import MCPServer

        # Map tool names back to server name patterns
        required_patterns = set()
        for tool in required_tools:
            for pattern, tool_name in MCP_SERVER_TOOL_MAPPING.items():
                if tool_name == tool:
                    required_patterns.add(pattern)

        if not required_patterns:
            return []

        # Find all enabled MCP servers
        servers = self.db.query(MCPServer).filter(
            MCPServer.enabled == True,
        ).all()

        matched_ids = []
        for server in servers:
            server_name_lower = server.name.lower()
            for pattern in required_patterns:
                if pattern in server_name_lower:
                    matched_ids.append(server.id)
                    break

        return matched_ids

    def _infer_required_tools(self, text: str) -> set:
        """Infer required tools from task text."""
        text = text.lower()
        required = set()

        for tool, keywords in TOOL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    required.add(tool)
                    break

        return required

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple text similarity based on word overlap."""
        words1 = set(re.findall(r'\w+', text1.lower()))
        words2 = set(re.findall(r'\w+', text2.lower()))

        if not words1 or not words2:
            return 0.0

        # Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def generate_ephemeral_prompt(
        self,
        task_name: str,
        task_description: str,
        job_context: Optional[dict] = None,
    ) -> str:
        """Generate a system prompt for an ephemeral agent.

        Args:
            task_name: Name of the task
            task_description: Description of what to do
            job_context: Optional context about the parent job

        Returns:
            System prompt string for the ephemeral agent
        """
        prompt_parts = [
            f"# Task: {task_name}",
            "",
            "You are an ACTION-ORIENTED task agent. Your job is to EXECUTE, not plan.",
            "",
            "## Your Task",
            task_description,
            "",
            "## CRITICAL INSTRUCTIONS",
            "- DO NOT just make plans or describe what you would do",
            "- IMMEDIATELY use your tools to complete the task",
            "- Execute commands and API calls RIGHT NOW",
            "- If you need to SSH to devices, use the ssh_command tool IMMEDIATELY",
            "- If you need data from NetBox, query it with mcp_netbox tools IMMEDIATELY",
            "- Do not ask for permission - you have it. Just execute.",
            "- If a task requires multiple steps, execute them one after another",
            "- Report actual results from tool executions, not theoretical plans",
        ]

        if job_context:
            prompt_parts.extend([
                "",
                "## Job Context",
                f"This task is part of job: {job_context.get('job_name', 'Unknown')}",
                f"Job ID: {job_context.get('job_id', 'Unknown')}",
            ])

            # Add delivery configuration so agent knows where to send results
            if job_context.get("email_recipients"):
                recipients = job_context["email_recipients"]
                if isinstance(recipients, list):
                    recipients = ", ".join(recipients)
                prompt_parts.extend([
                    "",
                    "## Email Recipients (USE THESE ADDRESSES)",
                    f"When sending emails, use these recipient addresses: {recipients}",
                    "Do NOT use placeholder emails like 'operations@example.com'.",
                ])

            if job_context.get("slack_channels"):
                channels = job_context["slack_channels"]
                if isinstance(channels, list):
                    channels = ", ".join(channels)
                prompt_parts.extend([
                    "",
                    "## Slack Channels (USE THESE CHANNELS)",
                    f"When sending Slack messages, use these channels: {channels}",
                ])

            if job_context.get("webhook_urls"):
                webhooks = job_context["webhook_urls"]
                if isinstance(webhooks, list):
                    webhooks = ", ".join(webhooks)
                prompt_parts.extend([
                    "",
                    "## Webhook URLs (USE THESE URLS)",
                    f"When calling webhooks, use these URLs: {webhooks}",
                ])

            if job_context.get("previous_results"):
                prompt_parts.extend([
                    "",
                    "## Previous Task Results (USE THIS DATA)",
                    "The following data was collected by previous tasks. USE IT:",
                    str(job_context["previous_results"]),
                ])

        prompt_parts.extend([
            "",
            "## Output Format",
            "Return ACTUAL results from your tool executions.",
            "Include the real data you collected, not plans or intentions.",
        ])

        return "\n".join(prompt_parts)

    def create_ephemeral_agent(
        self,
        task_name: str,
        task_description: str,
        job_id: int,
        tools: list,
        job_context: Optional[dict] = None,
    ) -> object:
        """Create an ephemeral agent for a task.

        Args:
            task_name: Name of the task
            task_description: Description
            job_id: Parent job ID
            tools: List of tool names to allow
            job_context: Optional context

        Returns:
            Created Agent object
        """
        from netagent_core.db import Agent
        from netagent_core.utils import get_setting

        prompt = self.generate_ephemeral_prompt(task_name, task_description, job_context)

        # Get MCP server IDs for any MCP-related tools
        mcp_tools = {t for t in tools if t.startswith("mcp_")}
        mcp_server_ids = self._get_mcp_server_ids_for_tools(mcp_tools) if mcp_tools else []

        # Get the default model from settings
        default_model = get_setting(self.db, "default_model", "gemini-2.5-flash")

        logger.info(f"Ephemeral agent for '{task_name}': tools={tools}, mcp_servers={mcp_server_ids}, model={default_model}")

        agent = Agent(
            name=f"ephemeral-{job_id}-{task_name[:20].replace(' ', '-').lower()}",
            description=f"Auto-generated agent for task: {task_name}",
            agent_type="ephemeral",
            system_prompt=prompt,
            model=default_model,  # Use the default model from settings
            allowed_tools=tools,
            mcp_server_ids=mcp_server_ids,  # Assign MCP servers based on required tools
            is_ephemeral=True,
            created_for_job_id=job_id,
            created_for_task_name=task_name,
            enabled=True,
        )

        self.db.add(agent)
        self.db.commit()

        logger.info(f"Created ephemeral agent {agent.id} for job {job_id} task '{task_name}' with MCP servers {mcp_server_ids}")

        return agent
