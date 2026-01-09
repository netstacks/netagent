"""MCP tool wrapper for agent executor.

Wraps MCP server tools as local tools that can be used by agents.
"""

import logging
from typing import Dict, Any, List, Optional

from ..llm.agent_executor import ToolDefinition
from ..mcp import MCPClient, MCPError

logger = logging.getLogger(__name__)


class MCPToolWrapper:
    """Wraps an MCP server tool for use with agent executor.

    This class takes a tool definition from an MCP server and creates
    a callable wrapper that routes execution to the MCP server.
    """

    def __init__(
        self,
        client: MCPClient,
        tool_def: Dict[str, Any],
        server_name: str,
    ):
        """Initialize MCP tool wrapper.

        Args:
            client: MCP client connected to the server
            tool_def: Tool definition from MCP server
            server_name: Name of the MCP server (for namespacing)
        """
        self.client = client
        self.tool_def = tool_def
        self.server_name = server_name

        # Build namespaced tool name
        original_name = tool_def.get("name", "unknown")
        self.name = f"mcp_{server_name}_{original_name}"
        self.original_name = original_name

        # Get tool metadata
        self.description = tool_def.get("description", f"MCP tool: {original_name}")
        self.parameters = tool_def.get("inputSchema", {
            "type": "object",
            "properties": {},
        })

        # Default risk level (can be overridden)
        self.risk_level = "medium"
        self.requires_approval = False

    async def execute(self, **kwargs) -> str:
        """Execute the MCP tool.

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool execution result as string
        """
        try:
            logger.info(f"Calling MCP tool {self.name} with args: {kwargs}")

            result = await self.client.call_tool(self.original_name, kwargs)

            # Format result for display
            if isinstance(result, dict):
                # Handle MCP content array format
                if "content" in result:
                    content_parts = []
                    for item in result.get("content", []):
                        if item.get("type") == "text":
                            content_parts.append(item.get("text", ""))
                        elif item.get("type") == "image":
                            content_parts.append("[Image content]")
                        elif item.get("type") == "resource":
                            content_parts.append(f"[Resource: {item.get('uri', 'unknown')}]")
                    return "\n".join(content_parts) if content_parts else str(result)
                return str(result)
            else:
                return str(result)

        except MCPError as e:
            logger.error(f"MCP tool error: {e}")
            return f"MCP Error: {e.message}"
        except Exception as e:
            logger.error(f"MCP tool execution failed: {e}")
            return f"Error executing MCP tool: {str(e)}"


def create_mcp_tool(
    client: MCPClient,
    tool_def: Dict[str, Any],
    server_name: str,
) -> ToolDefinition:
    """Create a ToolDefinition from an MCP tool.

    Args:
        client: MCP client
        tool_def: Tool definition from MCP server
        server_name: Server name for namespacing

    Returns:
        ToolDefinition for use with agent executor
    """
    wrapper = MCPToolWrapper(client, tool_def, server_name)

    return ToolDefinition(
        name=wrapper.name,
        description=wrapper.description,
        parameters=wrapper.parameters,
        handler=wrapper.execute,
        requires_approval=wrapper.requires_approval,
        risk_level=wrapper.risk_level,
    )


async def load_mcp_tools_for_agent(
    mcp_server_ids: List[int],
    db_session_factory,
    encryption_key: Optional[str] = None,
) -> List[ToolDefinition]:
    """Load all MCP tools for an agent from configured servers.

    Args:
        mcp_server_ids: IDs of MCP servers to load tools from
        db_session_factory: Factory for database sessions
        encryption_key: Key for decrypting auth tokens

    Returns:
        List of ToolDefinitions for all MCP tools
    """
    if not mcp_server_ids:
        return []

    tools = []

    with db_session_factory() as db:
        from ..db import MCPServer
        # Import directly from encryption module to avoid fastapi dependency in audit
        from ..utils.encryption import decrypt_value

        servers = db.query(MCPServer).filter(
            MCPServer.id.in_(mcp_server_ids),
            MCPServer.enabled == True,
        ).all()

        for server in servers:
            try:
                # Get auth token
                auth_token = None
                if server.auth_config_encrypted:
                    auth_token = decrypt_value(server.auth_config_encrypted)

                # Create client
                client = MCPClient(
                    base_url=server.base_url,
                    auth_type=server.auth_type,
                    auth_token=auth_token,
                )

                # Use cached tools if available, otherwise discover
                server_tools = server.tools or []
                if not server_tools:
                    logger.info(f"Discovering tools from MCP server: {server.name}")
                    server_tools = await client.list_tools()

                # Create tool definitions
                for tool_def in server_tools:
                    tools.append(create_mcp_tool(client, tool_def, server.name))

                logger.info(f"Loaded {len(server_tools)} tools from MCP server: {server.name}")

            except Exception as e:
                logger.error(f"Failed to load tools from MCP server {server.name}: {e}")
                # Continue with other servers

    return tools
