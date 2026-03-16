"""EagleView subscriber overlay tool.

Queries the EagleView API for satellite subscriber service chain data:
VNO, VWA, SMTS, OVS flows, goBGP routes, policy DB.
"""

import os
import json
import logging

import httpx

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)

EAGLEVIEW_DCS = ["naw03.spprod", "nac01.spprod"]


class EagleViewClient:

    def __init__(self):
        self.jwt_url = os.getenv("EV_JWT_URL", "https://jwt-ipv4.us-or.viasat.io/v1/token?stripe=dsc-prod&name=eagleview")
        self.jwt_basic = os.getenv("EV_JWT_BASIC", "")
        self.base_url = os.getenv("EV_BASE_URL", "https://eagleview.dsc-prod.viasat.io")
        self.verify = os.getenv("VERIFY_SSL", "false").lower() == "true"
        self.token = None

    def _get_token(self):
        r = httpx.get(self.jwt_url, headers={"Authorization": f"Basic {self.jwt_basic}"},
                      verify=self.verify, timeout=10.0)
        r.raise_for_status()
        self.token = r.text.strip()

    def _headers(self):
        if not self.token:
            self._get_token()
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, dc, path):
        r = httpx.get(f"{self.base_url}/{dc}/{path}", headers=self._headers(),
                      verify=self.verify, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def svcchain(self, dc, ip):
        return self._get(dc, f"api/svcchain/{ip}")

    def dscdiag(self, dc, ip):
        return self._get(dc, f"api/v2/dscdiag?ip={ip}&format=json")


_ev = None

def _get_ev():
    global _ev
    if _ev is None:
        _ev = EagleViewClient()
    return _ev


def create_eagleview_tool() -> ToolDefinition:
    async def handler(ip: str) -> str:
        try:
            ev = _get_ev()
            for dc in EAGLEVIEW_DCS:
                try:
                    r = ev.svcchain(dc, ip)
                    if isinstance(r, dict) and r.get("ip"):
                        vno = r.get("vno", {}).get("consul", {})
                        vwa = r.get("vwa", {})
                        smts = r.get("smts", {})
                        return (
                            f"EagleView subscriber found on {dc}:\n\n"
                            f"  IP: {r.get('ip')}\n"
                            f"  MAC: {r.get('mac')}\n"
                            f"  Online: {r.get('online')}\n"
                            f"  Modem: {r.get('modemType')}\n"
                            f"  DC: {r.get('dc')}\n"
                            f"  Active NC: {r.get('active_nc')}\n"
                            f"  Stack: {r.get('vasn', {}).get('stack')}\n"
                            f"  FL Path: {' → '.join(r.get('flpath', []))}\n"
                            f"  VNO: {vno.get('node_name')}\n"
                            f"  VLAN: {vno.get('data', {}).get('vlan')}\n"
                            f"  BGP Community: {vno.get('data', {}).get('bgpcommunity')}\n"
                            f"  VWA Host: {vwa.get('host')}\n"
                            f"  VWA State: {vwa.get('consul', {}).get('data', {}).get('nodestate')}\n"
                            f"  SMTS: {smts.get('consul', {}).get('node_name')}\n"
                            f"  SMTS DC: {smts.get('dc')}\n"
                            f"  OVS Flows: {vwa.get('ovs_flows', {}).get('count', 0)}\n"
                            f"  goBGP Routes: {len(r.get('bgp', [{}])[0].get('data', []))}\n"
                            f"  Policies: {r.get('policydb', {}).get('count', 0)}"
                        )
                except Exception:
                    pass

                try:
                    r = ev.dscdiag(dc, ip)
                    if isinstance(r, dict) and r.get("ip"):
                        vno = r.get("vno", {}).get("consul", {})
                        return (
                            f"EagleView (dscdiag) on {dc}:\n\n"
                            f"  IP: {r.get('ip')}\n"
                            f"  Online: {r.get('online')}\n"
                            f"  VNO: {vno.get('node_name')}\n"
                            f"  VLAN: {vno.get('data', {}).get('vlan')}"
                        )
                except Exception:
                    continue

            return f"Not found in EagleView on any DC"
        except Exception as e:
            return f"Error: {e}"

    return ToolDefinition(
        name="eagleview_lookup",
        description="Look up a subscriber IP in EagleView. Returns overlay service chain data: VNO, VWA, SMTS, OVS flows, goBGP routes, policies.",
        parameters={
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Subscriber IP to look up"},
            },
            "required": ["ip"],
        },
        handler=handler,
    )
