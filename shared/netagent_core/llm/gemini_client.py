"""Gemini API client for chat completions via Apigee.

Supports:
- Chat completions with message history
- Function/tool calling
- Streaming responses (SSE)
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Generator, AsyncGenerator

import httpx

from .apigee_token import ApigeeTokenManager, get_token_manager

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Represents a tool/function call from the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class GeminiResponse:
    """Response from Gemini API."""

    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class GeminiClient:
    """Client for Gemini API via Apigee.

    Usage:
        client = GeminiClient()
        response = client.chat([
            {"role": "user", "content": "Hello!"}
        ])
        print(response.content)
    """

    def __init__(
        self,
        token_manager: Optional[ApigeeTokenManager] = None,
        api_url: Optional[str] = None,
        model: str = "gemini-2.0-flash",
    ):
        """Initialize Gemini client.

        Args:
            token_manager: Token manager for Apigee OAuth (uses global if not provided)
            api_url: Gemini API URL via Apigee (or GEMINI_API_URL env var)
            model: Default model to use
        """
        self.token_manager = token_manager or get_token_manager()
        self.api_url = (api_url or os.getenv("GEMINI_API_URL", "")).rstrip("/")
        self.default_model = model

        if not self.api_url:
            raise ValueError("GEMINI_API_URL environment variable required")

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with current OAuth token."""
        token = self.token_manager.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _convert_messages_to_gemini(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Convert OpenAI-style messages to Gemini format.

        Returns:
            Tuple of (system_instruction, contents)
        """
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append({
                    "role": "user",
                    "parts": [{"text": content}]
                })
            elif role == "assistant":
                parts = []
                if content:
                    parts.append({"text": content})

                # Handle tool calls
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        parts.append({
                            "functionCall": {
                                "name": tc["function"]["name"],
                                "args": json.loads(tc["function"]["arguments"])
                                if isinstance(tc["function"]["arguments"], str)
                                else tc["function"]["arguments"]
                            }
                        })

                if parts:
                    contents.append({
                        "role": "model",
                        "parts": parts
                    })
            elif role == "tool":
                # Tool response
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.get("name", "unknown"),
                            "response": {"result": content}
                        }
                    }]
                })

        return system_instruction, contents

    def _convert_tools_to_gemini(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tools to Gemini function declarations."""
        function_declarations = []

        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                function_declarations.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {"type": "object", "properties": {}})
                })

        if function_declarations:
            return [{"functionDeclarations": function_declarations}]
        return []

    def _parse_response(self, data: Dict[str, Any]) -> GeminiResponse:
        """Parse Gemini API response."""
        content = None
        tool_calls = []
        finish_reason = None

        candidates = data.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")

            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    content = (content or "") + part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(ToolCall(
                        id=f"call_{len(tool_calls)}",
                        name=fc["name"],
                        arguments=fc.get("args", {})
                    ))

        # Parse usage
        usage = {}
        usage_data = data.get("usageMetadata", {})
        if usage_data:
            usage = {
                "prompt_tokens": usage_data.get("promptTokenCount", 0),
                "completion_tokens": usage_data.get("candidatesTokenCount", 0),
                "total_tokens": usage_data.get("totalTokenCount", 0),
            }

        return GeminiResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> GeminiResponse:
        """Send chat completion request.

        Args:
            messages: List of messages in OpenAI format
            model: Model to use (defaults to client's default)
            tools: Optional list of tools in OpenAI format
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens in response

        Returns:
            GeminiResponse with content and/or tool calls
        """
        model = model or self.default_model
        system_instruction, contents = self._convert_messages_to_gemini(messages)

        # Build request body
        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            body["tools"] = self._convert_tools_to_gemini(tools)

        # Make request
        url = f"{self.api_url}/models/{model}:generateContent"

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    url,
                    headers=self._get_headers(),
                    json=body,
                )

                # Handle 401 with token refresh
                if response.status_code == 401:
                    logger.info("Token expired, refreshing and retrying")
                    self.token_manager.force_refresh()
                    response = client.post(
                        url,
                        headers=self._get_headers(),
                        json=body,
                    )

                response.raise_for_status()
                return self._parse_response(response.json())

        except httpx.HTTPStatusError as e:
            logger.error(f"Gemini API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Gemini request failed: {e}")
            raise

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream chat completion response.

        Args:
            messages: List of messages in OpenAI format
            model: Model to use
            tools: Optional list of tools
            temperature: Sampling temperature
            max_tokens: Maximum tokens

        Yields:
            Streaming events with partial content or tool calls
        """
        model = model or self.default_model
        system_instruction, contents = self._convert_messages_to_gemini(messages)

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            body["tools"] = self._convert_tools_to_gemini(tools)

        url = f"{self.api_url}/models/{model}:streamGenerateContent?alt=sse"

        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream(
                    "POST",
                    url,
                    headers=self._get_headers(),
                    json=body,
                ) as response:
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            parsed = self._parse_response(data)

                            yield {
                                "type": "content" if parsed.content else "tool_call",
                                "content": parsed.content,
                                "tool_calls": [
                                    {
                                        "id": tc.id,
                                        "name": tc.name,
                                        "arguments": tc.arguments
                                    }
                                    for tc in parsed.tool_calls
                                ],
                                "finish_reason": parsed.finish_reason,
                                "usage": parsed.usage,
                            }

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield {"type": "error", "error": str(e)}

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> GeminiResponse:
        """Async chat completion request."""
        model = model or self.default_model
        system_instruction, contents = self._convert_messages_to_gemini(messages)

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            body["tools"] = self._convert_tools_to_gemini(tools)

        url = f"{self.api_url}/models/{model}:generateContent"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers=self._get_headers(),
                json=body,
            )

            if response.status_code == 401:
                self.token_manager.force_refresh()
                response = await client.post(
                    url,
                    headers=self._get_headers(),
                    json=body,
                )

            response.raise_for_status()
            return self._parse_response(response.json())

    async def achat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Async streaming chat completion."""
        model = model or self.default_model
        system_instruction, contents = self._convert_messages_to_gemini(messages)

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            body["tools"] = self._convert_tools_to_gemini(tools)

        url = f"{self.api_url}/models/{model}:streamGenerateContent?alt=sse"

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._get_headers(),
                json=body,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        parsed = self._parse_response(data)

                        yield {
                            "type": "content" if parsed.content else "tool_call",
                            "content": parsed.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "arguments": tc.arguments
                                }
                                for tc in parsed.tool_calls
                            ],
                            "finish_reason": parsed.finish_reason,
                            "usage": parsed.usage,
                        }
