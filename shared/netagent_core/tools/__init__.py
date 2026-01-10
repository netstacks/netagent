"""Tools module for agent capabilities."""

from .base import BaseTool, ToolResult
from .memory_tool import RecallMemoryTool, StoreMemoryTool
from .ssh_tool import SSHCommandTool, create_ssh_tool
from .knowledge_tool import KnowledgeSearchTool, create_knowledge_search_tool
from .mcp_tool import MCPToolWrapper, create_mcp_tool, load_mcp_tools_for_agent
from .api_resource_tool import APIResourceToolWrapper, create_api_resource_tool, load_api_resources_for_agent
from .email_tool import SendEmailTool, create_email_tool
from .handoff_tool import HandoffToAgentTool, create_handoff_tool
from .approval_tool import RequestApprovalTool, create_approval_tool

__all__ = [
    # Base
    "BaseTool",
    "ToolResult",
    # Memory
    "RecallMemoryTool",
    "StoreMemoryTool",
    # SSH
    "SSHCommandTool",
    "create_ssh_tool",
    # Knowledge
    "KnowledgeSearchTool",
    "create_knowledge_search_tool",
    # MCP
    "MCPToolWrapper",
    "create_mcp_tool",
    "load_mcp_tools_for_agent",
    # API Resources
    "APIResourceToolWrapper",
    "create_api_resource_tool",
    "load_api_resources_for_agent",
    # Email
    "SendEmailTool",
    "create_email_tool",
    # Handoff
    "HandoffToAgentTool",
    "create_handoff_tool",
    # Approval
    "RequestApprovalTool",
    "create_approval_tool",
]
