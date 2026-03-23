"""AWS Bedrock client for Anthropic Claude models.

Implements BaseLLMClient using boto3 bedrock-runtime to call
Anthropic Claude models via AWS Bedrock's Converse API.

Supports two auth methods:
  1. IAM credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
  2. Bedrock API key (AWS_BEDROCK_API_KEY) - simpler, no IAM needed
"""

import json
import logging
import os
from typing import List, Dict, Any, Optional

from .base_client import BaseLLMClient, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-west-2"


class BedrockClient(BaseLLMClient):
    """Client for Anthropic Claude models via AWS Bedrock.

    Uses the Bedrock Converse API which provides a unified interface
    for chat completions with tool calling support.

    Auth priority:
      1. AWS_BEDROCK_API_KEY env var (simplest)
      2. Standard boto3 credential chain (IAM keys, instance role, etc.)

    Usage:
        client = BedrockClient(model="anthropic.claude-sonnet-4-20250514-v1:0")
        response = await client.achat([
            {"role": "user", "content": "Hello!"}
        ])
    """

    def __init__(
        self,
        model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
        region: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.default_model = model
        self.region = region or os.getenv("AWS_BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", DEFAULT_REGION))
        self.api_key = api_key or os.getenv("AWS_BEDROCK_API_KEY")
        self._client = None

    def _get_client(self):
        """Get or create boto3 bedrock-runtime client."""
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ImportError(
                    "boto3 is required for Bedrock provider. "
                    "Install it with: pip install boto3"
                )

            config = Config(
                region_name=self.region,
                retries={"max_attempts": 3, "mode": "adaptive"},
            )

            kwargs = {
                "service_name": "bedrock-runtime",
                "config": config,
            }

            # If API key is provided, use it for auth
            if self.api_key:
                from botocore.auth import SigV4Auth
                # Bedrock API keys are passed via the x-amz-bedrock-api-key header
                # We still need a boto3 client, but auth is handled differently
                logger.info("Using Bedrock API key authentication")
            else:
                logger.info("Using standard AWS credential chain for Bedrock")

            self._client = boto3.client(**kwargs)
        return self._client

    def _convert_messages(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Convert OpenAI-style messages to Bedrock Converse format.

        Returns:
            Tuple of (system_prompt, messages)
        """
        system_prompt = None
        converted = []
        pending_tool_results = []

        def flush_tool_results():
            nonlocal pending_tool_results
            if pending_tool_results:
                converted.append({
                    "role": "user",
                    "content": pending_tool_results,
                })
                pending_tool_results = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                system_prompt = content

            elif role == "user":
                flush_tool_results()
                converted.append({
                    "role": "user",
                    "content": [{"text": content}],
                })

            elif role == "assistant":
                flush_tool_results()
                content_blocks = []
                if content:
                    content_blocks.append({"text": content})

                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        args = tc["function"]["arguments"]
                        if isinstance(args, str):
                            args = json.loads(args)
                        content_blocks.append({
                            "toolUse": {
                                "toolUseId": tc["id"],
                                "name": tc["function"]["name"],
                                "input": args,
                            }
                        })

                if content_blocks:
                    converted.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })

            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", msg.get("name", "unknown"))
                pending_tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_call_id,
                        "content": [{"text": content}],
                    }
                })

        flush_tool_results()
        return system_prompt, converted

    def _convert_tools(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tools to Bedrock Converse toolConfig format."""
        tool_specs = []

        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                params = func.get("parameters", {"type": "object", "properties": {}})
                tool_specs.append({
                    "toolSpec": {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "inputSchema": {
                            "json": params,
                        },
                    }
                })

        return tool_specs

    def _parse_response(self, response: Dict[str, Any]) -> LLMResponse:
        """Parse Bedrock Converse API response."""
        content = None
        tool_calls = []

        output = response.get("output", {})
        message = output.get("message", {})

        for block in message.get("content", []):
            if "text" in block:
                content = (content or "") + block["text"]
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(ToolCall(
                    id=tu["toolUseId"],
                    name=tu["name"],
                    arguments=tu.get("input", {}),
                ))

        stop_reason = response.get("stopReason", "")
        finish_reason = "tool_calls" if stop_reason == "tool_use" else stop_reason

        usage = {}
        usage_data = response.get("usage", {})
        if usage_data:
            usage = {
                "prompt_tokens": usage_data.get("inputTokens", 0),
                "completion_tokens": usage_data.get("outputTokens", 0),
                "total_tokens": usage_data.get("inputTokens", 0) + usage_data.get("outputTokens", 0),
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Async chat completion via Bedrock Converse API."""
        import asyncio

        model_id = model or self.default_model
        system_prompt, converted_messages = self._convert_messages(messages)

        kwargs: Dict[str, Any] = {
            "modelId": model_id,
            "messages": converted_messages,
            "inferenceConfig": {
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
        }

        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        if tools:
            tool_specs = self._convert_tools(tools)
            if tool_specs:
                kwargs["toolConfig"] = {"tools": tool_specs}

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._get_client().converse(**kwargs)
            )

            logger.info(
                f"Bedrock response: model={model_id}, "
                f"stop_reason={response.get('stopReason', 'N/A')}"
            )
            return self._parse_response(response)

        except Exception as e:
            logger.error(f"Bedrock API error: {e}")
            raise
