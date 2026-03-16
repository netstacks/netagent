"""A10 CGNAT tool for NAT session lookups.

Queries A10 load balancers directly via aXAPI v3.
Supports partition switching (VIASAT_RES_SUB, VIASAT_MOB).
"""

import os
import logging
from typing import Dict, Any

import httpx

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class A10Client:

    def __init__(self, host: str):
        self.base_url = f"https://{host}/axapi/v3"
        self.user = os.getenv("A10_USER", "cm-admin")
        self.password = os.getenv("A10_PASS", "")
        self.verify = os.getenv("VERIFY_SSL", "false").lower() == "true"
        self.token = None

    def _auth(self):
        r = httpx.post(f"{self.base_url}/auth",
                       json={"credentials": {"username": self.user, "password": self.password}},
                       verify=self.verify, timeout=10.0)
        r.raise_for_status()
        self.token = r.json()["authresponse"]["signature"]

    def _headers(self):
        if not self.token:
            self._auth()
        return {"Authorization": f"A10 {self.token}", "Accept": "application/json", "Content-Type": "application/json"}

    def _get(self, path):
        r = httpx.get(f"{self.base_url}/{path}", headers=self._headers(), verify=self.verify, timeout=15.0)
        r.raise_for_status()
        return r.json() if r.status_code != 204 else {}

    def _post(self, path, data=None):
        r = httpx.post(f"{self.base_url}/{path}", headers=self._headers(), json=data, verify=self.verify, timeout=15.0)
        r.raise_for_status()
        return r.json() if r.status_code != 204 else {}

    def switch_partition(self, name):
        self._post(f"active-partition/{name}")

    def partitions(self):
        return self._get("partition-all/oper").get("partition-all", {}).get("oper", {}).get("partition-list", [])

    def performance(self):
        return self._get("cgnv6/lsn/performance/oper").get("performance", {}).get("oper", {})

    def sessions(self):
        return self._get("cgnv6/lsn/user-quota-session/oper").get("user-quota-session", {}).get("oper", {}).get("session-list", [])

    def find_nat(self, inside_ip, partition="VIASAT_RES_SUB"):
        self.switch_partition(partition)
        for s in self.sessions():
            if s.get("inside-address") == inside_ip:
                return s
        return None


# Known A10 devices
A10_DEVICES = {
    "naw03": "cgnat01-den.naw03.spprod.viasat.io",
    "nac01": "cgnat01-chi.nac01.spprod.viasat.io",
    "den": "cgnat01-den.naw03.spprod.viasat.io",
    "chi": "cgnat01-chi.nac01.spprod.viasat.io",
}


def create_a10_cgnat_tool() -> ToolDefinition:
    async def handler(inside_ip: str, host: str = "cgnat01-den.naw03.spprod.viasat.io",
                      partition: str = "VIASAT_RES_SUB") -> str:
        try:
            # Try to resolve short names
            if host in A10_DEVICES:
                host = A10_DEVICES[host]

            client = A10Client(host)

            # Try specified partition first, then the other
            session = client.find_nat(inside_ip, partition)
            used_partition = partition
            if not session:
                other = "VIASAT_MOB" if partition == "VIASAT_RES_SUB" else "VIASAT_RES_SUB"
                session = client.find_nat(inside_ip, other)
                if session:
                    used_partition = other

            if session:
                return (
                    f"CGNAT session found on {host} (partition: {used_partition}):\n\n"
                    f"  Inside IP:  {session['inside-address']}\n"
                    f"  NAT IP:     {session['nat-address']}\n"
                    f"  Pool:       {session['nat-pool-name']}\n"
                    f"  Sessions:   {session['session-count']}\n"
                    f"  TCP Quota:  {session.get('tcp-quota', '?')}\n"
                    f"  UDP Quota:  {session.get('udp-quota', '?')}"
                )
            return f"No CGNAT session found for {inside_ip} on {host} (tried both partitions)"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="a10_cgnat_lookup",
        description="Look up a CGNAT NAT session on an A10 device. Shows inside-to-NAT IP mapping, pool, and session count. Auto-tries both partitions (residential and mobility).",
        parameters={
            "type": "object",
            "properties": {
                "inside_ip": {"type": "string", "description": "Inside (subscriber) IP to look up"},
                "host": {"type": "string", "description": "A10 FQDN (default: cgnat01-den.naw03.spprod.viasat.io)"},
                "partition": {"type": "string", "description": "A10 partition: VIASAT_RES_SUB or VIASAT_MOB"},
            },
            "required": ["inside_ip"],
        },
        handler=handler,
    )
