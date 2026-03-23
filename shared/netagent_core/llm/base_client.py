"""Abstract base class for LLM clients.

Provides a common interface for different LLM providers (Gemini, Bedrock/Anthropic, etc.)
so that AgentExecutor and other consumers can work with any provider transparently.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncGenerator


@dataclass
class ToolCall:
    """Represents a tool/function call from the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients.

    All LLM providers must implement achat() at minimum.
    Messages use OpenAI-style format as the common interchange:
      [{"role": "system"|"user"|"assistant"|"tool", "content": "...", ...}]

    Tools use OpenAI-style format:
      [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]
    """

    @abstractmethod
    async def achat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Async chat completion.

        Args:
            messages: Messages in OpenAI format
            model: Model override (or use client default)
            tools: Tools in OpenAI format
            temperature: Sampling temperature
            max_tokens: Max output tokens

        Returns:
            LLMResponse with content and/or tool calls
        """
        pass
