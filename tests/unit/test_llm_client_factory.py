"""Tests for LLM client factory and multi-provider support."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))

from netagent_core.llm.client_factory import (
    create_llm_client,
    detect_provider,
    PROVIDER_GEMINI,
    PROVIDER_BEDROCK,
)
from netagent_core.llm.base_client import BaseLLMClient, LLMResponse, ToolCall


class TestDetectProvider:
    def test_gemini_model(self):
        assert detect_provider("gemini-2.5-flash") == PROVIDER_GEMINI

    def test_gemini_model_old(self):
        assert detect_provider("gemini-2.0-flash") == PROVIDER_GEMINI

    def test_bedrock_anthropic_model(self):
        assert detect_provider("anthropic.claude-sonnet-4-20250514-v1:0") == PROVIDER_BEDROCK

    def test_bedrock_us_prefix(self):
        assert detect_provider("anthropic.claude-haiku-4-20250414-v1:0") == PROVIDER_BEDROCK

    def test_unknown_defaults_to_gemini(self):
        assert detect_provider("some-unknown-model") == PROVIDER_GEMINI


class TestCreateLLMClient:
    @patch.dict(os.environ, {
        "APIGEE_CLIENT_ID": "test",
        "APIGEE_CLIENT_SECRET": "test",
        "APIGEE_TOKEN_URL": "https://test.example.com/token",
        "GEMINI_API_URL": "https://test.example.com/gemini",
    })
    def test_create_gemini_client(self):
        from netagent_core.llm.gemini_client import GeminiClient
        client = create_llm_client(provider="gemini", model="gemini-2.5-flash")
        assert isinstance(client, GeminiClient)
        assert isinstance(client, BaseLLMClient)

    @patch("boto3.client")
    def test_create_bedrock_client(self, mock_boto3):
        from netagent_core.llm.bedrock_client import BedrockClient
        client = create_llm_client(provider="bedrock", model="anthropic.claude-sonnet-4-20250514-v1:0")
        assert isinstance(client, BedrockClient)
        assert isinstance(client, BaseLLMClient)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_client(provider="openai", model="gpt-4")

    @patch.dict(os.environ, {
        "APIGEE_CLIENT_ID": "test",
        "APIGEE_CLIENT_SECRET": "test",
        "APIGEE_TOKEN_URL": "https://test.example.com/token",
        "GEMINI_API_URL": "https://test.example.com/gemini",
    })
    def test_auto_detect_gemini(self):
        from netagent_core.llm.gemini_client import GeminiClient
        client = create_llm_client(model="gemini-2.5-flash")  # No provider specified
        assert isinstance(client, GeminiClient)

    @patch("boto3.client")
    def test_auto_detect_bedrock(self, mock_boto3):
        from netagent_core.llm.bedrock_client import BedrockClient
        client = create_llm_client(model="anthropic.claude-sonnet-4-20250514-v1:0")
        assert isinstance(client, BedrockClient)


class TestLLMResponse:
    def test_has_tool_calls_false(self):
        response = LLMResponse(content="Hello")
        assert response.has_tool_calls is False

    def test_has_tool_calls_true(self):
        response = LLMResponse(
            content="Let me check",
            tool_calls=[ToolCall(id="1", name="ssh_command", arguments={"command": "show ip route"})],
        )
        assert response.has_tool_calls is True
