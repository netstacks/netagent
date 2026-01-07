"""MCP (Model Context Protocol) HTTP client.

Communicates with MCP servers over HTTP/SSE transport.
"""

import uuid
import logging
from typing import Dict, Any, List, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPError(Exception):
    """MCP protocol error."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP Error {code}: {message}")


class MCPClient:
    """HTTP client for MCP servers.

    Usage:
        client = MCPClient("http://netbox-mcp:8000")
        await client.initialize()
        tools = await client.list_tools()
        result = await client.call_tool("netbox_get_objects", {...})
    """

    def __init__(
        self,
        base_url: str,
        auth_type: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: float = 60.0,
    ):
        """Initialize MCP client.

        Args:
            base_url: MCP server base URL
            auth_type: Authentication type ("bearer", "basic", or None)
            auth_token: Authentication token/credentials
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.auth_token = auth_token
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._initialized = False

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
        }

        if self.session_id:
            headers["X-MCP-Session-ID"] = self.session_id

        if self.auth_type == "bearer" and self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        elif self.auth_type == "basic" and self.auth_token:
            headers["Authorization"] = f"Basic {self.auth_token}"

        return headers

    async def _request(self, method: str, params: Optional[Dict] = None) -> Any:
        """Send JSON-RPC request to MCP server.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Result from server

        Raises:
            MCPError: On protocol error
        """
        request_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/mcp",
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()

                data = response.json()

                # Check for error response
                if "error" in data:
                    error = data["error"]
                    raise MCPError(
                        code=error.get("code", -1),
                        message=error.get("message", "Unknown error"),
                        data=error.get("data"),
                    )

                return data.get("result")

            except httpx.HTTPStatusError as e:
                logger.error(f"MCP HTTP error: {e.response.status_code}")
                raise MCPError(-1, f"HTTP {e.response.status_code}: {e.response.text}")
            except httpx.RequestError as e:
                logger.error(f"MCP request error: {e}")
                raise MCPError(-1, f"Request failed: {str(e)}")

    async def initialize(self) -> Dict[str, Any]:
        """Initialize MCP session.

        Returns:
            Server capabilities and info
        """
        result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "clientInfo": {
                "name": "netagent",
                "version": "1.0.0",
            }
        })

        self.session_id = result.get("sessionId")
        self._initialized = True

        logger.info(f"MCP session initialized: {self.session_id}")
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Get available tools from MCP server.

        Returns:
            List of tool definitions
        """
        if not self._initialized:
            await self.initialize()

        result = await self._request("tools/list")
        return result.get("tools", [])

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        if not self._initialized:
            await self.initialize()

        result = await self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        return result

    async def health_check(self) -> bool:
        """Check if MCP server is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self._get_headers(),
                )
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"MCP health check failed: {e}")
            return False

    def convert_tool_to_openai_format(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MCP tool definition to OpenAI function format.

        Args:
            tool: MCP tool definition

        Returns:
            OpenAI-style function definition
        """
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {
                    "type": "object",
                    "properties": {},
                }),
            }
        }


class MCPClientSync:
    """Synchronous MCP client wrapper for non-async contexts."""

    def __init__(self, *args, **kwargs):
        self._async_client = MCPClient(*args, **kwargs)

    def initialize(self) -> Dict[str, Any]:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_client.initialize()
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_client.list_tools()
        )

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_client.call_tool(tool_name, arguments)
        )

    def health_check(self) -> bool:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_client.health_check()
        )
