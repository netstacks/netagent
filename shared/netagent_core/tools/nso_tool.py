"""NSO network device query tools.

Provides access to Juniper (via JSON-RPC) and Arista (via RESTCONF)
devices managed by Cisco NSO.

Tools:
  - nso_juniper_route: Route lookup on a Juniper device
  - nso_juniper_lldp: LLDP neighbor discovery on a Juniper device
  - nso_juniper_vrfs: List VRFs on a Juniper device
  - nso_arista_exec: Run CLI commands on an Arista device
"""

import os
import json
import logging
from typing import Dict, Any

import httpx

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class NSOClient:
    """NSO API client supporting RESTCONF (Arista) and JSON-RPC (Juniper)."""

    def __init__(self):
        self.base_url = os.getenv("NSO_BASE_URL", "https://nso.gi-nw.viasat.io")
        self.user = os.getenv("NSO_USER", "cm-admin")
        self.password = os.getenv("NSO_PASS", "")
        self.verify = os.getenv("VERIFY_SSL", "false").lower() == "true"

    # ---- RESTCONF (Arista/Cisco XR) ----

    def arista_exec(self, device: str, command: str) -> str:
        url = (
            f"{self.base_url}/restconf/data/tailf-ncs:devices"
            f"/device={device}/live-status/tailf-ned-arista-dcs-stats:exec/any"
        )
        response = httpx.post(
            url, json={"input": {"args": command}},
            auth=(self.user, self.password), verify=self.verify,
            headers={"Accept": "application/yang-data+json", "Content-Type": "application/yang-data+json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json().get("tailf-ned-arista-dcs-stats:output", {}).get("result", "")

    # ---- JSON-RPC (Juniper) ----

    def _jsonrpc(self, cookies, method, params, call_id=1):
        response = httpx.post(
            f"{self.base_url}/jsonrpc",
            json={"jsonrpc": "2.0", "id": call_id, "method": method, "params": params},
            auth=(self.user, self.password), verify=self.verify,
            cookies=cookies, headers={"Content-Type": "application/json"}, timeout=30.0,
        )
        response.raise_for_status()
        cookies.update(dict(response.cookies))
        body = response.json()
        if "error" in body:
            raise RuntimeError(f"NSO JSON-RPC error: {body['error']}")
        return body.get("result")

    def _get_session(self):
        cookies = {}
        self._jsonrpc(cookies, "login", {"user": self.user, "passwd": self.password}, 1)
        result = self._jsonrpc(cookies, "new_read_trans", {"db": "running"}, 2)
        return cookies, result["th"]

    def juniper_rpc(self, device: str, rpc_name: str, action_name: str, params: dict) -> list:
        cookies, th = self._get_session()
        path = f"/ncs:devices/device{{{device}}}/rpc/jrpc:{rpc_name}/{action_name}"
        return self._jsonrpc(cookies, "run_action", {"th": th, "path": path, "params": params}, 3)

    def juniper_route_lookup(self, device, destination, table):
        return self.juniper_rpc(device, "rpc-get-route-information", "get-route-information",
                                {"destination": destination, "table": table})

    def juniper_lldp(self, device):
        return self.juniper_rpc(device, "rpc-get-lldp-interface-neighbors", "get-lldp-interface-neighbors", {})

    def juniper_instances(self, device):
        return self.juniper_rpc(device, "rpc-get-instance-information", "get-instance-information", {})


# Singleton
_nso = None

def _get_nso():
    global _nso
    if _nso is None:
        _nso = NSOClient()
    return _nso


def _resolve_device(name: str) -> str:
    """Resolve short name to FQDN via NetBox MCP if needed."""
    if "." in name:
        return name
    # Try NetBox
    try:
        from .netbox_mcp_tool import _get_netbox_mcp
        nb = _get_netbox_mcp()
        result = nb.get_objects("dcim.device", {"name__ic": name}, fields=["name"], limit=1)
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                data = json.loads(item["text"])
                if data.get("results"):
                    return data["results"][0]["name"]
    except Exception:
        pass
    return name


def _parse_routes(raw):
    """Parse Juniper route RPC output into readable text."""
    lines = []
    for item in raw:
        name = item.get("name", "").split("/")[-1]
        value = item.get("value", "")
        if value and name in ("table-name", "rt-destination", "active-tag", "protocol-name",
                              "to", "via", "as-path", "mpls-label", "preference"):
            lines.append(f"  {name}: {value}")
        elif "selected-next-hop" in item.get("name", "") and not value:
            lines.append("  [SELECTED]")
    return "\n".join(lines) or "No data"


def _parse_lldp(raw):
    """Parse Juniper LLDP into readable text."""
    neighbors = []
    current = {}
    for item in raw:
        leaf = item.get("name", "").split("/")[-1]
        val = item.get("value", "")
        if leaf == "lldp-local-port-id":
            if current.get("remote"):
                neighbors.append(current)
            current = {"local": val}
        elif leaf == "lldp-local-parent-interface-name":
            current["parent"] = val
        elif leaf == "lldp-remote-system-name":
            current["remote"] = val
        elif leaf == "lldp-remote-port-id":
            current["remote_port"] = val
    if current.get("remote"):
        neighbors.append(current)

    # Group by remote device
    grouped = {}
    for n in neighbors:
        rn = n["remote"]
        grouped.setdefault(rn, []).append(n)

    lines = []
    for dev, entries in sorted(grouped.items()):
        ports = [e.get("local", "?") for e in entries[:4]]
        more = f"... +{len(entries)-4}" if len(entries) > 4 else ""
        lines.append(f"  {dev} ({len(entries)} links) [{', '.join(ports)}{more}]")
    return "\n".join(lines) or "No LLDP neighbors"


# ---- Tool factory functions ----

def create_nso_route_tool() -> ToolDefinition:
    async def handler(device: str, destination: str, vrf: str = "master") -> str:
        try:
            fqdn = _resolve_device(device)
            raw = _get_nso().juniper_route_lookup(fqdn, destination, vrf)
            return f"Route lookup: {destination} in {vrf} on {fqdn}\n\n{_parse_routes(raw)}"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="nso_juniper_route",
        description="Look up a route on a Juniper device in a specific VRF. Shows best path with next-hop, interface, AS path, MPLS labels.",
        parameters={
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name or FQDN (e.g. dcar01-den). Resolved via NetBox."},
                "destination": {"type": "string", "description": "IP to look up"},
                "vrf": {"type": "string", "description": "VRF name (e.g. EXEDE_SUB, INTERNET)"},
            },
            "required": ["device", "destination", "vrf"],
        },
        handler=handler,
    )


def create_nso_lldp_tool() -> ToolDefinition:
    async def handler(device: str, interface: str = "") -> str:
        try:
            fqdn = _resolve_device(device)
            raw = _get_nso().juniper_lldp(fqdn)
            result = _parse_lldp(raw)
            if interface:
                # Filter to specific interface
                base = interface.split(".")[0]
                filtered = [n for n in raw if base in str(n.get("value", ""))]
                if filtered:
                    result = f"LLDP on {interface}:\n" + _parse_lldp(filtered)
            return f"LLDP neighbors on {fqdn}:\n\n{result}"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="nso_juniper_lldp",
        description="Get LLDP neighbors on a Juniper device. Shows connected devices and ports.",
        parameters={
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name or FQDN"},
                "interface": {"type": "string", "description": "Optional: filter to specific interface (e.g. ae13)"},
            },
            "required": ["device"],
        },
        handler=handler,
    )


def create_nso_vrfs_tool() -> ToolDefinition:
    async def handler(device: str) -> str:
        try:
            fqdn = _resolve_device(device)
            raw = _get_nso().juniper_instances(fqdn)
            vrfs = [item["value"] for item in raw if item.get("name", "").endswith("instance-name") and item.get("value")]
            return f"VRFs on {fqdn}:\n\n" + "\n".join(f"  {v}" for v in vrfs)
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="nso_juniper_vrfs",
        description="List all VRFs (routing instances) on a Juniper device.",
        parameters={
            "type": "object",
            "properties": {"device": {"type": "string", "description": "Device name or FQDN"}},
            "required": ["device"],
        },
        handler=handler,
    )


def create_nso_arista_exec_tool() -> ToolDefinition:
    async def handler(device: str, command: str) -> str:
        try:
            fqdn = _resolve_device(device)
            result = _get_nso().arista_exec(fqdn, command)
            return f"Output from {fqdn}:\n\n{result}"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="nso_arista_exec",
        description="Run a CLI command on an Arista switch via NSO. For show commands like 'show lldp neighbors', 'show mac address-table'.",
        parameters={
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Arista device name or FQDN"},
                "command": {"type": "string", "description": "CLI command to run"},
            },
            "required": ["device", "command"],
        },
        handler=handler,
    )
