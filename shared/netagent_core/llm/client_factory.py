"""LLM client factory for multi-provider support.

Creates the appropriate LLM client based on provider and model configuration.
"""

import logging
from typing import Optional

from .base_client import BaseLLMClient

logger = logging.getLogger(__name__)

# Provider constants
PROVIDER_GEMINI = "gemini"
PROVIDER_BEDROCK = "bedrock"

# Model prefix to provider mapping for auto-detection
MODEL_PROVIDER_PREFIXES = {
    "anthropic.": PROVIDER_BEDROCK,
    "gemini-": PROVIDER_GEMINI,
}


def detect_provider(model: str) -> str:
    """Auto-detect provider from model name.

    Args:
        model: Model identifier string

    Returns:
        Provider string ("gemini" or "bedrock")
    """
    for prefix, provider in MODEL_PROVIDER_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    # Default to gemini for backward compatibility
    return PROVIDER_GEMINI


def create_llm_client(
    provider: Optional[str] = None,
    model: str = "gemini-2.0-flash",
) -> BaseLLMClient:
    """Create an LLM client for the given provider and model.

    Args:
        provider: LLM provider ("gemini" or "bedrock"). If None, auto-detected from model name.
        model: Model identifier

    Returns:
        Configured LLM client instance

    Raises:
        ValueError: If provider is unknown
    """
    if provider is None:
        provider = detect_provider(model)

    if provider == PROVIDER_GEMINI:
        from .gemini_client import GeminiClient
        return GeminiClient(model=model)

    elif provider == PROVIDER_BEDROCK:
        from .bedrock_client import BedrockClient
        return BedrockClient(model=model)

    else:
        raise ValueError(f"Unknown LLM provider: {provider}. Supported: {PROVIDER_GEMINI}, {PROVIDER_BEDROCK}")
