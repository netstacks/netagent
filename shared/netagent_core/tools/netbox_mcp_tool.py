"""NetBox MCP tool for device inventory lookups.

Uses the NetBox MCP server for device, IP, VRF, and VNO lookups.
"""

import os
import json
import logging

import httpx

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class MCPClient:
    """Generic HTTP MCP client."""

    def __init__(self, url):
        self.url = url
        self.session_id = None
        self._next_id = 1

    def _parse_sse(self, text):
        for line in text.strip().split("\n"):
            if line.startswith("data: "):
                return json.loads(line[6:])
        return {}

    def _post(self, payload):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = httpx.post(self.url, json=payload, headers=headers, timeout=30.0)
        r.raise_for_status()
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return self._parse_sse(r.text)

    def _get_id(self):
        self._next_id += 1
        return self._next_id

    def connect(self):
        self._post({"jsonrpc": "2.0", "id": self._get_id(), "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "netagent", "version": "1.0"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name, args):
        if not self.session_id:
            self.connect()
        result = self._post({"jsonrpc": "2.0", "id": self._get_id(), "method": "tools/call",
                             "params": {"name": name, "arguments": args}})
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")
        return result.get("result", {})


class NetBoxMCP:

    def __init__(self, url):
        self.client = MCPClient(url)

    def search(self, query, limit=5):
        return self.client.call_tool("netbox_search_objects", {"query": query, "limit": limit})

    def get_objects(self, obj_type, filters, fields=None, limit=5):
        args = {"object_type": obj_type, "filters": filters, "limit": limit}
        if fields:
            args["fields"] = fields
        return self.client.call_tool("netbox_get_objects", args)


_nb = None

def _get_netbox_mcp():
    global _nb
    if _nb is None:
        url = os.getenv("NETBOX_MCP_URL", "http://internal-apitools-elb-cw-1573458742.us-east-1.elb.amazonaws.com:8000/mcp")
        _nb = NetBoxMCP(url)
    return _nb


def create_netbox_search_tool() -> ToolDefinition:
    async def handler(query: str) -> str:
        try:
            nb = _get_netbox_mcp()
            result = nb.search(query, limit=10)
            content = result.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    data = json.loads(item["text"])
                    lines = []
                    for obj_type, results in data.items():
                        if results:
                            lines.append(f"\n{obj_type}: {len(results)} results")
                            for r in results[:5]:
                                name = r.get("name", r.get("address", str(r)[:80]))
                                lines.append(f"  {name}")
                    return "\n".join(lines) or "No results"
            return "No results"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="netbox_search",
        description="Search NetBox for devices, IPs, VRFs, VLANs, or any network inventory data.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search term"}},
            "required": ["query"],
        },
        handler=handler,
    )
