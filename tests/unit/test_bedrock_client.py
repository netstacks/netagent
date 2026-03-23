"""Tests for Bedrock client message/tool format conversion."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))

from netagent_core.llm.bedrock_client import BedrockClient
from netagent_core.llm.base_client import LLMResponse


@pytest.fixture
def client():
    with patch("boto3.client"):
        return BedrockClient(model="anthropic.claude-sonnet-4-20250514-v1:0")


class TestConvertMessages:
    def test_system_message_extracted(self, client):
        messages = [
            {"role": "system", "content": "You are a network engineer."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = client._convert_messages(messages)
        assert system == "You are a network engineer."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_user_message(self, client):
        messages = [{"role": "user", "content": "Check BGP"}]
        system, converted = client._convert_messages(messages)
        assert system is None
        assert converted[0]["content"] == [{"text": "Check BGP"}]

    def test_assistant_message_with_tool_calls(self, client):
        messages = [
            {"role": "assistant", "content": "Let me check", "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "ssh_command",
                        "arguments": '{"command": "show ip bgp summary"}',
                    },
                }
            ]},
        ]
        system, converted = client._convert_messages(messages)
        assert len(converted) == 1
        content = converted[0]["content"]
        assert content[0] == {"text": "Let me check"}
        assert "toolUse" in content[1]
        assert content[1]["toolUse"]["name"] == "ssh_command"

    def test_tool_results_grouped(self, client):
        messages = [
            {"role": "tool", "tool_call_id": "call_1", "name": "ssh_command", "content": "BGP is up"},
            {"role": "tool", "tool_call_id": "call_2", "name": "ping", "content": "Success"},
        ]
        system, converted = client._convert_messages(messages)
        # Both tool results should be in a single user message
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert len(converted[0]["content"]) == 2
        assert "toolResult" in converted[0]["content"][0]
        assert "toolResult" in converted[0]["content"][1]


class TestConvertTools:
    def test_convert_openai_tools(self, client):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ssh_command",
                    "description": "Run SSH command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                        },
                        "required": ["command"],
                    },
                },
            }
        ]
        result = client._convert_tools(tools)
        assert len(result) == 1
        spec = result[0]["toolSpec"]
        assert spec["name"] == "ssh_command"
        assert spec["description"] == "Run SSH command"
        assert "json" in spec["inputSchema"]


class TestParseResponse:
    def test_parse_text_response(self, client):
        response = {
            "output": {
                "message": {
                    "content": [{"text": "BGP is healthy."}],
                },
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }
        result = client._parse_response(response)
        assert isinstance(result, LLMResponse)
        assert result.content == "BGP is healthy."
        assert result.has_tool_calls is False
        assert result.usage["prompt_tokens"] == 100
        assert result.usage["completion_tokens"] == 50
        assert result.usage["total_tokens"] == 150

    def test_parse_tool_call_response(self, client):
        response = {
            "output": {
                "message": {
                    "content": [
                        {"text": "Let me check."},
                        {
                            "toolUse": {
                                "toolUseId": "tu_123",
                                "name": "ssh_command",
                                "input": {"command": "show bgp summary"},
                            }
                        },
                    ],
                },
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }
        result = client._parse_response(response)
        assert result.content == "Let me check."
        assert result.has_tool_calls is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "ssh_command"
        assert result.tool_calls[0].id == "tu_123"
        assert result.tool_calls[0].arguments == {"command": "show bgp summary"}
