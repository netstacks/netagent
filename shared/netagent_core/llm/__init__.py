"""LLM module - multi-provider support for Gemini and Bedrock/Anthropic."""

from .base_client import BaseLLMClient, LLMResponse, ToolCall
from .apigee_token import ApigeeTokenManager
from .gemini_client import GeminiClient, GeminiResponse
from .client_factory import create_llm_client, detect_provider, PROVIDER_GEMINI, PROVIDER_BEDROCK
from .agent_executor import AgentExecutor, AgentAction, AgentEvent, ToolDefinition

# Lazy import for BedrockClient - boto3 may not be installed
def __getattr__(name):
    if name == "BedrockClient":
        from .bedrock_client import BedrockClient
        return BedrockClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Base
    "BaseLLMClient",
    "LLMResponse",
    "ToolCall",
    # Apigee
    "ApigeeTokenManager",
    # Gemini
    "GeminiClient",
    "GeminiResponse",
    # Bedrock (lazy)
    "BedrockClient",
    # Factory
    "create_llm_client",
    "detect_provider",
    "PROVIDER_GEMINI",
    "PROVIDER_BEDROCK",
    # Agent
    "AgentExecutor",
    "AgentAction",
    "AgentEvent",
    "ToolDefinition",
]
