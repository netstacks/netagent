"""ReAct Agent Executor with tool calling support.

Implements the ReAct (Reasoning and Acting) pattern:
1. Observe: Receive input/observation
2. Think: Reason about what to do
3. Act: Execute a tool or provide final answer
4. Repeat until task is complete
"""

import json
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncGenerator, Callable, Awaitable
from datetime import datetime

from .gemini_client import GeminiClient, GeminiResponse, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Definition of an available tool."""

    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Awaitable[str]]
    requires_approval: bool = False
    risk_level: str = "low"  # low, medium, high


@dataclass
class AgentAction:
    """Represents an action taken by the agent."""

    action_type: str  # thought, tool_call, tool_result, final_answer, error
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    reasoning: Optional[str] = None
    content: Optional[str] = None
    error: Optional[str] = None
    risk_level: Optional[str] = None
    requires_approval: bool = False
    duration_ms: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentEvent:
    """Event emitted during agent execution for streaming."""

    event_type: str  # thinking, tool_call, tool_result, content, done, error, approval_required
    data: Dict[str, Any] = field(default_factory=dict)


class AgentExecutor:
    """Execute an AI agent with ReAct-style reasoning and tool use.

    Usage:
        executor = AgentExecutor(
            client=GeminiClient(),
            system_prompt="You are a network engineer assistant...",
            tools=[ssh_tool, search_tool],
        )

        async for event in executor.run("Check BGP status on router-1"):
            if event.event_type == "content":
                print(event.data["content"])
    """

    def __init__(
        self,
        client: GeminiClient,
        system_prompt: str,
        tools: List[ToolDefinition] = None,
        max_iterations: int = 10,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        approval_callback: Optional[Callable[[AgentAction], Awaitable[bool]]] = None,
    ):
        """Initialize agent executor.

        Args:
            client: Gemini API client
            system_prompt: System prompt defining agent behavior
            tools: Available tools for the agent
            max_iterations: Maximum reasoning iterations
            temperature: LLM temperature
            max_tokens: Max tokens per response
            approval_callback: Async callback for approval requests (returns True to approve)
        """
        self.client = client
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in (tools or [])}
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.approval_callback = approval_callback

        # Conversation history
        self.messages: List[Dict[str, Any]] = []
        self.actions: List[AgentAction] = []

    def _build_system_prompt(self) -> str:
        """Build enhanced system prompt with tool instructions."""
        if not self.tools:
            return self.system_prompt

        tool_descriptions = []
        for tool in self.tools.values():
            tool_descriptions.append(f"- {tool.name}: {tool.description}")

        return f"""{self.system_prompt}

You have access to the following tools:
{chr(10).join(tool_descriptions)}

When you need to use a tool, call it using the function calling capability.
After receiving tool results, analyze them and either:
1. Call another tool if more information is needed
2. Provide a final answer to the user

Always explain your reasoning before taking actions.
If a task cannot be completed, explain why clearly."""

    def _get_tools_for_api(self) -> List[Dict[str, Any]]:
        """Convert tools to OpenAI-style format for API."""
        if not self.tools:
            return []

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            }
            for tool in self.tools.values()
        ]

    async def _execute_tool(self, tool_call: ToolCall) -> AgentAction:
        """Execute a tool and return the result."""
        start_time = time.time()

        tool = self.tools.get(tool_call.name)
        if not tool:
            return AgentAction(
                action_type="tool_result",
                tool_name=tool_call.name,
                tool_input=tool_call.arguments,
                error=f"Unknown tool: {tool_call.name}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Check if approval is required
        if tool.requires_approval and self.approval_callback:
            action = AgentAction(
                action_type="tool_call",
                tool_name=tool_call.name,
                tool_input=tool_call.arguments,
                risk_level=tool.risk_level,
                requires_approval=True,
            )

            approved = await self.approval_callback(action)
            if not approved:
                return AgentAction(
                    action_type="tool_result",
                    tool_name=tool_call.name,
                    tool_input=tool_call.arguments,
                    error="Action was not approved",
                    risk_level=tool.risk_level,
                    requires_approval=True,
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        try:
            result = await tool.handler(**tool_call.arguments)
            return AgentAction(
                action_type="tool_result",
                tool_name=tool_call.name,
                tool_input=tool_call.arguments,
                tool_output=result,
                risk_level=tool.risk_level,
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return AgentAction(
                action_type="tool_result",
                tool_name=tool_call.name,
                tool_input=tool_call.arguments,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def run(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent with a user message.

        Args:
            user_message: The user's input message
            context: Optional context to include in the conversation

        Yields:
            AgentEvent objects for streaming updates
        """
        # Add user message to history
        self.messages.append({
            "role": "user",
            "content": user_message,
        })

        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # Emit thinking event
            yield AgentEvent(
                event_type="thinking",
                data={"iteration": iteration, "message": f"Thinking... (step {iteration})"}
            )

            # Build messages for API
            api_messages = [
                {"role": "system", "content": self._build_system_prompt()}
            ] + self.messages

            try:
                # Call LLM
                logger.debug(f"[Iteration {iteration}] Calling LLM with {len(api_messages)} messages")
                response = await self.client.achat(
                    messages=api_messages,
                    tools=self._get_tools_for_api() if self.tools else None,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                logger.info(f"[Iteration {iteration}] LLM response: has_tool_calls={response.has_tool_calls}, content_len={len(response.content) if response.content else 0}, finish_reason={response.finish_reason}")

                # Handle tool calls
                if response.has_tool_calls:
                    # Record the assistant's response with tool calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                }
                            }
                            for tc in response.tool_calls
                        ]
                    }
                    self.messages.append(assistant_msg)

                    # If there's reasoning before tool calls, emit it
                    if response.content:
                        yield AgentEvent(
                            event_type="reasoning",
                            data={"content": response.content}
                        )
                        self.actions.append(AgentAction(
                            action_type="thought",
                            reasoning=response.content,
                        ))

                    # Execute each tool call
                    for tool_call in response.tool_calls:
                        tool = self.tools.get(tool_call.name)

                        # Emit tool call event
                        yield AgentEvent(
                            event_type="tool_call",
                            data={
                                "name": tool_call.name,
                                "arguments": tool_call.arguments,
                                "risk_level": tool.risk_level if tool else "unknown",
                                "requires_approval": tool.requires_approval if tool else False,
                            }
                        )

                        # Check approval if needed
                        if tool and tool.requires_approval:
                            yield AgentEvent(
                                event_type="approval_required",
                                data={
                                    "tool_name": tool_call.name,
                                    "arguments": tool_call.arguments,
                                    "risk_level": tool.risk_level,
                                }
                            )

                            if self.approval_callback:
                                action = AgentAction(
                                    action_type="tool_call",
                                    tool_name=tool_call.name,
                                    tool_input=tool_call.arguments,
                                    risk_level=tool.risk_level,
                                    requires_approval=True,
                                )
                                approved = await self.approval_callback(action)
                                if not approved:
                                    # Add rejection to messages
                                    self.messages.append({
                                        "role": "tool",
                                        "tool_call_id": tool_call.id,
                                        "name": tool_call.name,
                                        "content": "Error: Action was not approved by user",
                                    })
                                    yield AgentEvent(
                                        event_type="tool_result",
                                        data={
                                            "name": tool_call.name,
                                            "error": "Action was not approved",
                                        }
                                    )
                                    continue

                        # Execute tool
                        action_result = await self._execute_tool(tool_call)
                        self.actions.append(action_result)

                        # Add tool result to messages
                        result_content = action_result.tool_output or action_result.error or "No output"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result_content,
                        })

                        # Emit tool result event
                        yield AgentEvent(
                            event_type="tool_result",
                            data={
                                "name": tool_call.name,
                                "result": action_result.tool_output,
                                "error": action_result.error,
                                "duration_ms": action_result.duration_ms,
                            }
                        )

                    # Continue loop to process tool results
                    logger.info(f"[Iteration {iteration}] Tool calls processed, continuing to next iteration")
                    continue

                # No tool calls - this is the final response
                logger.info(f"[Iteration {iteration}] No tool calls, emitting final response")
                if response.content:
                    logger.info(f"[Iteration {iteration}] Content to emit: {response.content[:200]}...")
                    self.messages.append({
                        "role": "assistant",
                        "content": response.content,
                    })

                    self.actions.append(AgentAction(
                        action_type="final_answer",
                        content=response.content,
                    ))

                    # Emit content event
                    yield AgentEvent(
                        event_type="content",
                        data={"content": response.content}
                    )

                # Done
                yield AgentEvent(
                    event_type="done",
                    data={
                        "iterations": iteration,
                        "usage": response.usage,
                        "actions_count": len(self.actions),
                    }
                )
                return

            except Exception as e:
                logger.error(f"Agent execution error: {e}")
                self.actions.append(AgentAction(
                    action_type="error",
                    error=str(e),
                ))
                yield AgentEvent(
                    event_type="error",
                    data={"error": str(e)}
                )
                return

        # Max iterations reached
        yield AgentEvent(
            event_type="error",
            data={"error": f"Max iterations ({self.max_iterations}) reached"}
        )

    def get_actions(self) -> List[AgentAction]:
        """Get all actions taken during execution."""
        return self.actions

    def get_messages(self) -> List[Dict[str, Any]]:
        """Get conversation history."""
        return self.messages

    def clear(self):
        """Clear conversation history and actions."""
        self.messages = []
        self.actions = []
