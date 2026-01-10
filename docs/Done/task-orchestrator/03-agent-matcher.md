# Phase 3: Agent Matcher

## Task 3.1: Create Smart Agent Matcher

**Files:**
- Create: `shared/netagent_core/job/agent_matcher.py`
- Modify: `shared/netagent_core/job/__init__.py`

### Step 1: Create the agent matcher

Create `shared/netagent_core/job/agent_matcher.py`:

```python
"""Smart agent matching for job tasks.

Matches tasks to existing agents based on:
1. Explicit agent name in task spec
2. Semantic similarity of task description to agent descriptions
3. Required tools for the task
"""

import logging
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from netagent_core.db import Agent

logger = logging.getLogger(__name__)


# Tool keywords that help identify required capabilities
TOOL_KEYWORDS = {
    "ssh_command": ["ssh", "device", "router", "switch", "show", "command", "cli"],
    "search_knowledge": ["knowledge", "documentation", "search", "find info", "look up"],
    "send_email": ["email", "mail", "send to", "notify via email"],
    "mcp_netbox": ["netbox", "inventory", "device list", "dcim", "ipam"],
    "handoff_to_agent": ["delegate", "hand off", "another agent"],
    "request_approval": ["approval", "confirm", "verify with human"],
}

# Agent type keywords
AGENT_TYPE_KEYWORDS = {
    "network": ["network", "router", "switch", "firewall", "device", "ssh", "cli"],
    "netbox": ["netbox", "inventory", "dcim", "ipam", "device list"],
    "diagnostic": ["diagnose", "troubleshoot", "debug", "analyze", "check"],
    "reporting": ["report", "summary", "aggregate", "table", "format"],
}


class AgentMatcher:
    """Matches job tasks to appropriate agents."""

    def __init__(self, db: Session):
        self.db = db

    def find_best_agent(
        self,
        task_name: str,
        task_description: str,
        agent_hint: Optional[str] = None,
        required_tools: Optional[list] = None,
    ) -> Tuple[Optional[Agent], float, str]:
        """Find the best matching agent for a task.

        Args:
            task_name: Name of the task
            task_description: Full task description
            agent_hint: Optional explicit agent name hint
            required_tools: Optional list of required tool names

        Returns:
            Tuple of (agent, confidence_score, reason)
            If no match found, returns (None, 0.0, reason)
        """
        # 1. Check explicit agent hint first
        if agent_hint and agent_hint.lower() != "auto":
            agent = self._find_by_name(agent_hint)
            if agent:
                return (agent, 1.0, f"Explicit match: {agent_hint}")
            else:
                logger.warning(f"Agent hint '{agent_hint}' not found, will search")

        # 2. Get all enabled agents
        agents = self.db.query(Agent).filter(
            Agent.enabled == True,
            Agent.is_ephemeral == False,
        ).all()

        if not agents:
            return (None, 0.0, "No agents available")

        # 3. Score each agent
        best_agent = None
        best_score = 0.0
        best_reason = ""

        combined_text = f"{task_name} {task_description}".lower()

        for agent in agents:
            score, reason = self._score_agent(agent, combined_text, required_tools)

            if score > best_score:
                best_score = score
                best_agent = agent
                best_reason = reason

        # 4. Return if above threshold
        MATCH_THRESHOLD = 0.4

        if best_score >= MATCH_THRESHOLD:
            return (best_agent, best_score, best_reason)
        else:
            return (None, best_score, f"No agent scored above threshold ({best_score:.2f})")

    def _find_by_name(self, name: str) -> Optional[Agent]:
        """Find agent by exact or partial name match."""
        agent = self.db.query(Agent).filter(
            Agent.name.ilike(name),
            Agent.enabled == True,
        ).first()

        if agent:
            return agent

        agent = self.db.query(Agent).filter(
            Agent.name.ilike(f"%{name}%"),
            Agent.enabled == True,
        ).first()

        return agent

    def _score_agent(
        self,
        agent: Agent,
        task_text: str,
        required_tools: Optional[list] = None,
    ) -> Tuple[float, str]:
        """Score how well an agent matches a task."""
        scores = []
        reasons = []

        # Score 1: Name match (0.3 weight)
        name_score = self._text_similarity(agent.name.lower(), task_text)
        if name_score > 0.3:
            scores.append(name_score * 0.3)
            reasons.append(f"name match ({name_score:.2f})")

        # Score 2: Description match (0.3 weight)
        if agent.description:
            desc_score = self._text_similarity(agent.description.lower(), task_text)
            if desc_score > 0.2:
                scores.append(desc_score * 0.3)
                reasons.append(f"description match ({desc_score:.2f})")

        # Score 3: Agent type match (0.2 weight)
        if agent.agent_type:
            type_keywords = AGENT_TYPE_KEYWORDS.get(agent.agent_type, [])
            type_score = sum(1 for kw in type_keywords if kw in task_text) / max(len(type_keywords), 1)
            if type_score > 0:
                scores.append(type_score * 0.2)
                reasons.append(f"type match ({type_score:.2f})")

        # Score 4: Tool capability match (0.2 weight)
        if agent.allowed_tools:
            inferred_tools = self._infer_required_tools(task_text)
            if required_tools:
                inferred_tools.update(required_tools)

            if inferred_tools:
                agent_tools = set(agent.allowed_tools)
                tool_match = len(inferred_tools & agent_tools) / len(inferred_tools)
                if tool_match > 0:
                    scores.append(tool_match * 0.2)
                    reasons.append(f"tools ({tool_match:.2f})")

        total_score = sum(scores)
        reason = ", ".join(reasons) if reasons else "no significant matches"

        return (total_score, reason)

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Simple word overlap similarity."""
        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        return len(intersection) / len(words1)

    def _infer_required_tools(self, task_text: str) -> set:
        """Infer required tools from task description."""
        required = set()

        for tool, keywords in TOOL_KEYWORDS.items():
            if any(kw in task_text for kw in keywords):
                required.add(tool)

        return required

    def generate_ephemeral_prompt(
        self,
        task_name: str,
        task_description: str,
        job_context: dict = None,
    ) -> str:
        """Generate a system prompt for an ephemeral agent."""
        context_str = ""
        if job_context:
            context_str = f"\n\nJob Context:\n{job_context}"

        return f"""You are a specialized agent created for a specific task within a larger job.

## Your Task
**{task_name}**

{task_description}

## Instructions
1. Focus ONLY on completing the specific task described above
2. Be efficient and direct - this is one step in a larger workflow
3. Return structured results that can be aggregated with other tasks
4. If you encounter errors, report them clearly but continue if possible
5. Do not ask clarifying questions - work with the information provided
{context_str}

## Output Format
Provide your results in a clear, structured format. If processing multiple items, use consistent formatting for each result.
"""
```

### Step 2: Update job module exports

Update `shared/netagent_core/job/__init__.py`:

```python
"""Job orchestration module."""

from .parser import JobSpecParser, ParsedJobSpec, ParsedTask
from .agent_matcher import AgentMatcher

__all__ = ["JobSpecParser", "ParsedJobSpec", "ParsedTask", "AgentMatcher"]
```

### Step 3: Commit

```bash
git add shared/netagent_core/job/
git commit -m "feat(job): add smart agent matcher"
```

---

## Verification

### 1. Test Agent Matching

```bash
python3 -c "
from netagent_core.db import get_db_context, Agent
from netagent_core.job import AgentMatcher

with get_db_context() as db:
    matcher = AgentMatcher(db)

    # Test tool inference
    tools = matcher._infer_required_tools('ssh to router and run show version')
    assert 'ssh_command' in tools
    print('✓ Tool inference works')

    # Test ephemeral prompt
    prompt = matcher.generate_ephemeral_prompt('Test Task', 'Do something')
    assert 'Test Task' in prompt
    print('✓ Ephemeral prompt generation works')

    # Test matching (if agents exist)
    agent = db.query(Agent).filter(Agent.enabled == True).first()
    if agent:
        found, score, reason = matcher.find_best_agent('test', 'test', agent_hint=agent.name)
        assert found.id == agent.id
        print(f'✓ Explicit match works: {agent.name}')
"
```

### Expected Outcomes

- [ ] Infers required tools from task description
- [ ] Generates focused ephemeral prompts
- [ ] Matches agents by explicit hint (score 1.0)
- [ ] Falls back to scoring when no hint provided
