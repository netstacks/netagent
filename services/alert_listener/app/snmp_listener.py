"""SNMP trap listener - receives SNMP v2c traps and forwards to API."""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://api:8001")
SNMP_PORT = int(os.getenv("SNMP_PORT", "1162"))
SNMP_COMMUNITY = os.getenv("SNMP_COMMUNITY", "public")


async def _forward_trap(trap_data: dict):
    """Forward an SNMP trap to the API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{API_URL}/api/alerts/ingest/snmp",
                json=trap_data,
            )
            if response.status_code != 200:
                logger.warning(f"API returned {response.status_code} for SNMP trap ingest")
        except Exception as e:
            logger.error(f"Failed to forward SNMP trap to API: {e}")


class SNMPTrapProtocol(asyncio.DatagramProtocol):
    """UDP SNMP trap receiver.

    Parses basic SNMP v2c trap PDUs. For production use with complex MIB
    resolution, consider using pysnmp's full async engine instead.
    """

    def __init__(self):
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info(f"SNMP trap listener ready on port {SNMP_PORT}")

    def datagram_received(self, data, addr):
        source_ip = addr[0]
        logger.info(f"Received SNMP trap from {source_ip} ({len(data)} bytes)")

        try:
            trap_data = self._parse_trap(data, source_ip)
            if trap_data:
                asyncio.ensure_future(_forward_trap(trap_data))
        except Exception as e:
            logger.error(f"Error processing SNMP trap from {source_ip}: {e}")

    def _parse_trap(self, data: bytes, source_ip: str) -> dict:
        """Basic SNMP v2c trap parsing.

        For a production deployment, use pysnmp's proper BER decoder.
        This provides a basic fallback that captures the raw data.
        """
        try:
            from pysnmp.hlapi import SnmpEngine
            from pysnmp.proto import api as snmp_api
            from pysnmp.proto.rfc1902 import ObjectIdentifier
            from pyasn1.codec.ber import decoder as ber_decoder
            from pysnmp.proto import rfc2c

            # Attempt to decode as SNMPv2c
            msg, _ = ber_decoder.decode(data, asn1Spec=rfc2c.Message())
            pdu = snmp_api.apiMessage.getPDU(msg)
            community = str(snmp_api.apiMessage.getCommunity(msg))

            varbinds = []
            trap_oid = ""

            for oid, val in snmp_api.apiTrapPDU.getVarBinds(pdu) if hasattr(snmp_api, 'apiTrapPDU') else snmp_api.apiPDU.getVarBinds(pdu):
                oid_str = str(oid)
                val_str = str(val)

                # snmpTrapOID.0
                if oid_str == "1.3.6.1.6.3.1.1.4.1.0":
                    trap_oid = val_str
                else:
                    varbinds.append({"oid": oid_str, "value": val_str})

            return {
                "source_ip": source_ip,
                "oid": trap_oid,
                "community": community,
                "varbinds": varbinds,
                "hostname": source_ip,
            }

        except ImportError:
            logger.warning("pysnmp not fully available, using raw capture mode")
            return {
                "source_ip": source_ip,
                "oid": "unknown",
                "community": "unknown",
                "varbinds": [],
                "hostname": source_ip,
                "raw_hex": data.hex()[:500],
            }
        except Exception as e:
            logger.warning(f"Failed to decode SNMP trap, using raw mode: {e}")
            return {
                "source_ip": source_ip,
                "oid": "unknown",
                "community": "unknown",
                "varbinds": [],
                "hostname": source_ip,
                "raw_hex": data.hex()[:500],
            }


async def start_snmp_listener():
    """Start the SNMP trap UDP listener."""
    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        SNMPTrapProtocol,
        local_addr=("0.0.0.0", SNMP_PORT),
    )

    logger.info(f"SNMP trap listener started on UDP port {SNMP_PORT}")
    return transport
