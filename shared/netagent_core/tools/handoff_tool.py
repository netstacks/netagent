"""Handoff tool for agent-to-agent delegation."""

import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable

from .base import BaseTool

logger = logging.getLogger(__name__)

# Maximum nesting depth for handoffs to prevent infinite loops
MAX_HANDOFF_DEPTH = 3


class HandoffToAgentTool(BaseTool):
    """Tool for handing off tasks to specialist agents.

    This tool allows an agent to delegate a task to another agent,
    passing context and receiving the result. The child agent's
    execution is fully visible in the parent's conversation through
    SSE events.
    """

    name = "handoff_to_agent"
    description = """Hand off a task to a specialist agent.
Use this when the current task requires expertise from another agent.
The target agent will receive your task summary and context, execute their work,
and return their results to you.

Always provide:
1. A clear task_summary describing what the target agent should accomplish
2. Relevant context including any information gathered so far

The handoff is synchronous - you will wait for and receive the agent's response."""

    parameters = {
        "type": "object",
        "properties": {
            "target_agent_id": {
                "type": "integer",
                "description": "ID of the agent to hand off to"
            },
            "target_agent_name": {
                "type": "string",
                "description": "Name of the agent (alternative to ID)"
            },
            "task_summary": {
                "type": "string",
                "description": "Clear description of what the target agent should accomplish"
            },
            "context": {
                "type": "object",
                "description": "Relevant context to pass (devices involved, previous findings, constraints)",
                "default": {}
            }
        },
        "required": ["task_summary"]
    }

    requires_approval = False
    risk_level = "low"

    def __init__(
        self,
        db_session_factory: Callable,
        parent_session_id: int,
        event_callback: Callable[[Any], Awaitable[None]],
        current_depth: int = 0,
        allowed_agent_ids: Optional[List[int]] = None,
    ):
        """Initialize handoff tool.

        Args:
            db_session_factory: Factory to create database sessions
            parent_session_id: ID of the parent AgentSession
            event_callback: Async callback to emit SSE events
            current_depth: Current handoff nesting depth
            allowed_agent_ids: If set, only these agent IDs can be handed off to
        """
        self.db_session_factory = db_session_factory
        self.parent_session_id = parent_session_id
        self.event_callback = event_callback
        self.current_depth = current_depth
        self.allowed_agent_ids = allowed_agent_ids

    async def execute(
        self,
        task_summary: str,
        target_agent_id: Optional[int] = None,
        target_agent_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Execute handoff to another agent.

        Args:
            task_summary: What the target agent should do
            target_agent_id: ID of target agent (optional if name provided)
            target_agent_name: Name of target agent (optional if ID provided)
            context: Context data to pass to the target agent

        Returns:
            String result from the target agent
        """
        from netagent_core.db import Agent, AgentSession
        from netagent_core.llm import GeminiClient, AgentExecutor, AgentEvent, ToolDefinition

        start_time = time.time()
        context = context or {}

        # Check depth limit
        if self.current_depth >= MAX_HANDOFF_DEPTH:
            return f"Error: Maximum handoff depth ({MAX_HANDOFF_DEPTH}) reached. Cannot delegate further."

        # Resolve target agent
        with self.db_session_factory() as db:
            if target_agent_id:
                agent = db.query(Agent).filter(
                    Agent.id == target_agent_id,
                    Agent.enabled == True
                ).first()
            elif target_agent_name:
                agent = db.query(Agent).filter(
                    Agent.name.ilike(f"%{target_agent_name}%"),
                    Agent.enabled == True
                ).first()
            else:
                return "Error: Must specify either target_agent_id or target_agent_name"

            if not agent:
                return f"Error: Target agent not found or disabled"

            # Check if this agent is allowed
            if self.allowed_agent_ids and agent.id not in self.allowed_agent_ids:
                return f"Error: Agent '{agent.name}' is not in the allowed handoff targets"

            # Prevent self-handoff
            parent_session = db.query(AgentSession).get(self.parent_session_id)
            if parent_session and parent_session.agent_id == agent.id:
                return "Error: Cannot hand off to yourself"

            # Create child session
            child_session = AgentSession(
                agent_id=agent.id,
                parent_session_id=self.parent_session_id,
                status="active",
                trigger_type="handoff",
                handoff_context=context,
                context={"task": task_summary},
                user_id=parent_session.user_id if parent_session else None,
            )
            db.add(child_session)
            db.commit()
            db.refresh(child_session)
            child_session_id = child_session.id

            # Extract agent config
            agent_config = {
                "id": agent.id,
                "name": agent.name,
                "model": agent.model,
                "system_prompt": agent.system_prompt,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
                "max_iterations": agent.max_iterations,
                "allowed_tools": agent.allowed_tools or [],
                "allowed_device_patterns": agent.allowed_device_patterns or ["*"],
                "knowledge_base_ids": agent.knowledge_base_ids or [],
                "mcp_server_ids": agent.mcp_server_ids or [],
                "allowed_handoff_agent_ids": agent.allowed_handoff_agent_ids,
            }

        # Emit handoff_start event
        await self.event_callback(AgentEvent(
            event_type="handoff_start",
            data={
                "parent_session_id": self.parent_session_id,
                "child_session_id": child_session_id,
                "target_agent": {
                    "id": agent_config["id"],
                    "name": agent_config["name"],
                },
                "task_summary": task_summary,
                "context": context,
                "depth": self.current_depth + 1,
            }
        ))

        try:
            # Build child agent tools
            tools = await self._build_child_tools(
                agent_config,
                child_session_id,
            )

            # Build the message for the child agent
            child_message = self._build_child_message(task_summary, context)

            # Create child executor
            client = GeminiClient(model=agent_config["model"])
            child_executor = AgentExecutor(
                client=client,
                system_prompt=agent_config["system_prompt"],
                tools=tools,
                max_iterations=agent_config["max_iterations"],
                temperature=agent_config["temperature"],
                max_tokens=agent_config["max_tokens"],
            )

            # Run child agent, forwarding all events
            final_result = ""
            async for event in child_executor.run(child_message):
                # Wrap and forward events
                await self.event_callback(AgentEvent(
                    event_type="handoff_event",
                    data={
                        "child_session_id": child_session_id,
                        "depth": self.current_depth + 1,
                        "agent_name": agent_config["name"],
                        "inner_event": {
                            "type": event.event_type,
                            **event.data
                        }
                    }
                ))

                if event.event_type == "content":
                    final_result += event.data.get("content", "")

            duration_ms = int((time.time() - start_time) * 1000)

            # Emit completion
            await self.event_callback(AgentEvent(
                event_type="handoff_complete",
                data={
                    "child_session_id": child_session_id,
                    "result_summary": final_result[:500] if final_result else "Completed",
                    "depth": self.current_depth + 1,
                    "duration_ms": duration_ms,
                }
            ))

            # Update child session status
            with self.db_session_factory() as db:
                session = db.query(AgentSession).get(child_session_id)
                if session:
                    session.status = "completed"
                    session.completed_at = datetime.utcnow()
                    db.commit()

            return final_result or "The agent completed the task but provided no response."

        except Exception as e:
            logger.exception(f"Handoff execution error: {e}")

            # Emit error event
            await self.event_callback(AgentEvent(
                event_type="handoff_error",
                data={
                    "child_session_id": child_session_id,
                    "error": str(e),
                    "depth": self.current_depth + 1,
                }
            ))

            # Update child session status
            with self.db_session_factory() as db:
                session = db.query(AgentSession).get(child_session_id)
                if session:
                    session.status = "failed"
                    session.completed_at = datetime.utcnow()
                    db.commit()

            return f"Handoff failed: {str(e)}"

    async def _build_child_tools(
        self,
        agent_config: Dict[str, Any],
        child_session_id: int,
    ) -> List["ToolDefinition"]:
        """Build tools for the child agent."""
        import os
        from netagent_core.tools import (
            create_ssh_tool,
            create_knowledge_search_tool,
            load_mcp_tools_for_agent,
        )
        from netagent_core.llm import ToolDefinition

        tools = []
        encryption_key = os.getenv("ENCRYPTION_KEY")

        # SSH tool - create_ssh_tool already returns a ToolDefinition
        if "ssh_command" in agent_config.get("allowed_tools", []):
            ssh_tool = create_ssh_tool(
                db_session_factory=self.db_session_factory,
                allowed_device_patterns=agent_config.get("allowed_device_patterns", ["*"]),
                encryption_key=encryption_key,
            )
            tools.append(ssh_tool)

        # Knowledge search tool - create_knowledge_search_tool already returns a ToolDefinition
        if "search_knowledge" in agent_config.get("allowed_tools", []):
            kb_ids = agent_config.get("knowledge_base_ids", [])
            if kb_ids:
                kb_tool = create_knowledge_search_tool(
                    db_session_factory=self.db_session_factory,
                    knowledge_base_ids=kb_ids,
                )
                tools.append(kb_tool)

        # Nested handoff tool (with incremented depth)
        if "handoff_to_agent" in agent_config.get("allowed_tools", []):
            handoff_tool = HandoffToAgentTool(
                db_session_factory=self.db_session_factory,
                parent_session_id=child_session_id,
                event_callback=self.event_callback,
                current_depth=self.current_depth + 1,
                allowed_agent_ids=agent_config.get("allowed_handoff_agent_ids"),
            )
            tools.append(ToolDefinition(
                name=handoff_tool.name,
                description=handoff_tool.description,
                parameters=handoff_tool.parameters,
                handler=handoff_tool.execute,
                requires_approval=handoff_tool.requires_approval,
                risk_level=handoff_tool.risk_level,
            ))

        # Load MCP tools for the child agent
        mcp_server_ids = agent_config.get("mcp_server_ids", [])
        if mcp_server_ids:
            try:
                mcp_tools = await load_mcp_tools_for_agent(
                    mcp_server_ids=mcp_server_ids,
                    db_session_factory=self.db_session_factory,
                    encryption_key=encryption_key,
                )
                tools.extend(mcp_tools)
                logger.debug(f"Loaded {len(mcp_tools)} MCP tools for child agent")
            except Exception as e:
                logger.error(f"Failed to load MCP tools for child agent: {e}")
                # Continue without MCP tools

        return tools

    def _build_child_message(
        self,
        task_summary: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        """Build the initial message for the child agent."""
        message = f"Task: {task_summary}"

        if context:
            message += "\n\nContext provided by the requesting agent:\n"
            for key, value in context.items():
                if isinstance(value, dict):
                    value = str(value)
                message += f"- {key}: {value}\n"

        message += "\nPlease complete this task and provide your findings."

        return message


def create_handoff_tool(
    db_session_factory: Callable,
    parent_session_id: int,
    event_callback: Callable[[Any], Awaitable[None]],
    current_depth: int = 0,
    allowed_agent_ids: Optional[List[int]] = None,
) -> HandoffToAgentTool:
    """Factory function to create a handoff tool.

    Args:
        db_session_factory: Factory to create database sessions
        parent_session_id: ID of the parent AgentSession
        event_callback: Async callback to emit SSE events
        current_depth: Current handoff nesting depth
        allowed_agent_ids: If set, only these agent IDs can be handed off to

    Returns:
        Configured HandoffToAgentTool instance
    """
    return HandoffToAgentTool(
        db_session_factory=db_session_factory,
        parent_session_id=parent_session_id,
        event_callback=event_callback,
        current_depth=current_depth,
        allowed_agent_ids=allowed_agent_ids,
    )
