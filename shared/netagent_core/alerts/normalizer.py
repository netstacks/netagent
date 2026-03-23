"""Alert normalizer - converts diverse alert formats into a common schema.

Supports syslog (RFC 3164/5424), Splunk webhooks, SNMP traps, and generic webhooks.
"""

import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Syslog severity mapping (RFC 5424)
SYSLOG_SEVERITIES = {
    0: "critical",   # Emergency
    1: "critical",   # Alert
    2: "critical",   # Critical
    3: "major",      # Error
    4: "warning",    # Warning
    5: "minor",      # Notice
    6: "info",       # Informational
    7: "info",       # Debug
}

# Common syslog message patterns for alert type classification
SYSLOG_PATTERNS = [
    (r"(?i)(interface|link).*(down|failed)", "interface_down"),
    (r"(?i)(interface|link).*(up|recovered)", "interface_up"),
    (r"(?i)bgp.*(down|reset|closed|idle)", "bgp_peer_down"),
    (r"(?i)bgp.*(established|up)", "bgp_peer_up"),
    (r"(?i)ospf.*neighbor.*(down|dead|lost)", "ospf_neighbor_down"),
    (r"(?i)ospf.*neighbor.*(up|full|established)", "ospf_neighbor_up"),
    (r"(?i)(cpu|processor).*(high|threshold|exceeded)", "high_cpu"),
    (r"(?i)(memory|mem).*(high|threshold|exceeded|low)", "high_memory"),
    (r"(?i)(power|psu).*(fail|down|fault)", "power_failure"),
    (r"(?i)(fan|cooling).*(fail|fault|alarm)", "fan_failure"),
    (r"(?i)(temperature|temp).*(high|critical|alarm)", "high_temperature"),
    (r"(?i)(config|configuration).*(change|modify|commit)", "config_change"),
    (r"(?i)(auth|login|ssh|tacacs).*(fail|denied|reject)", "auth_failure"),
    (r"(?i)(reboot|restart|reload)", "device_reboot"),
    (r"(?i)(flap|bouncing|unstable)", "link_flap"),
    (r"(?i)(err-disable|errdisable)", "errdisable"),
    (r"(?i)(duplex|speed).*mismatch", "duplex_mismatch"),
    (r"(?i)(storm.control|broadcast.storm)", "storm_control"),
]

# Extract device/interface from syslog
DEVICE_PATTERNS = [
    r"(?i)(?:interface|port|link)\s+(\S+)",
    r"(?i)(?:Gi|Te|Eth|xe-|ge-|et-|ae)\S+",
]


def compute_correlation_key(alert_data: Dict[str, Any]) -> str:
    """Compute a correlation key for deduplication.

    Groups alerts by device + alert_type so we can detect flapping,
    storms, and repeated events.
    """
    device = alert_data.get("device_name", "unknown")
    alert_type = alert_data.get("alert_type", "unknown")
    raw = f"{device}|{alert_type}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _classify_syslog_message(message: str) -> str:
    """Classify a syslog message into an alert type."""
    for pattern, alert_type in SYSLOG_PATTERNS:
        if re.search(pattern, message):
            return alert_type
    return "generic_syslog"


def _extract_interface(message: str) -> Optional[str]:
    """Extract interface name from a syslog message."""
    # Common interface naming patterns
    match = re.search(
        r"((?:Gi|Te|Fo|Hu|Eth|xe-|ge-|et-|ae|Po|Vl|Lo|mgmt)\S+)",
        message,
    )
    if match:
        return match.group(1)
    return None


def _severity_from_string(s: str) -> str:
    """Normalize a severity string to our standard set."""
    s = s.lower().strip()
    mapping = {
        "emergency": "critical", "emerg": "critical",
        "alert": "critical",
        "critical": "critical", "crit": "critical",
        "error": "major", "err": "major",
        "warning": "warning", "warn": "warning",
        "notice": "minor",
        "informational": "info", "info": "info",
        "debug": "info",
        "high": "critical",
        "medium": "major",
        "low": "minor",
    }
    return mapping.get(s, "info")


def normalize_syslog(
    raw: str,
    facility: int = 0,
    severity: int = 6,
    source_ip: str = "",
) -> Dict[str, Any]:
    """Normalize a syslog message into alert format.

    Args:
        raw: Raw syslog message string
        facility: Syslog facility code
        severity: Syslog severity code (0=emergency, 7=debug)
        source_ip: IP address of the syslog sender

    Returns:
        Dict matching Alert model fields
    """
    # Parse hostname from syslog (RFC 3164: <PRI>TIMESTAMP HOSTNAME MSG)
    hostname = source_ip
    message = raw

    # Try RFC 3164 format: <PRI>Mon DD HH:MM:SS HOSTNAME MSG
    rfc3164_match = re.match(
        r"(?:<\d+>)?\s*\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+(\S+)\s+(.*)",
        raw,
    )
    if rfc3164_match:
        hostname = rfc3164_match.group(1)
        message = rfc3164_match.group(2)
    else:
        # Try RFC 5424: <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID MSG
        rfc5424_match = re.match(
            r"(?:<\d+>)\d*\s+\S+\s+(\S+)\s+\S+\s+\S+\s+\S+\s+(.*)",
            raw,
        )
        if rfc5424_match:
            hostname = rfc5424_match.group(1)
            message = rfc5424_match.group(2)

    alert_type = _classify_syslog_message(message)
    interface = _extract_interface(message)
    norm_severity = SYSLOG_SEVERITIES.get(severity, "info")

    result = {
        "source_type": "syslog",
        "severity": norm_severity,
        "alert_type": alert_type,
        "title": message[:200].strip(),
        "description": message,
        "device_name": hostname,
        "device_ip": source_ip,
        "interface_name": interface,
        "raw_data": {
            "raw_message": raw,
            "facility": facility,
            "severity_code": severity,
            "source_ip": source_ip,
        },
        "occurred_at": datetime.utcnow().isoformat(),
    }
    result["correlation_key"] = compute_correlation_key(result)
    return result


def normalize_splunk(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Splunk saved search webhook payload.

    Splunk sends alerts via webhook with structure:
    {
        "result": {...},    # First matching result
        "search_name": "",  # Name of the saved search
        "app": "",          # Splunk app name
        "owner": "",
        "results_link": "", # Link to full results
        "sid": "",          # Search job ID
    }
    """
    result = payload.get("result", {})
    search_name = payload.get("search_name", "Unknown Splunk Alert")

    # Try common Splunk field names for device/severity
    device_name = (
        result.get("host") or result.get("hostname") or
        result.get("device") or result.get("device_name") or
        result.get("src") or ""
    )
    device_ip = result.get("src_ip") or result.get("ip") or ""
    interface = result.get("interface") or result.get("interface_name") or None

    severity_raw = (
        result.get("severity") or result.get("urgency") or
        result.get("priority") or payload.get("severity") or "info"
    )
    severity = _severity_from_string(str(severity_raw))

    alert_type = result.get("alert_type") or result.get("event_type") or "splunk_alert"
    description = result.get("_raw") or result.get("message") or str(result)

    normalized = {
        "source_type": "splunk",
        "source_name": payload.get("app", "splunk"),
        "severity": severity,
        "alert_type": alert_type,
        "title": f"[Splunk] {search_name}",
        "description": description[:2000],
        "device_name": device_name,
        "device_ip": device_ip,
        "interface_name": interface,
        "raw_data": payload,
    }
    normalized["correlation_key"] = compute_correlation_key(normalized)
    return normalized


def normalize_snmp_trap(trap_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an SNMP trap into alert format.

    Expected trap_data structure (from pysnmp or similar):
    {
        "source_ip": "10.1.1.1",
        "oid": "1.3.6.1.6.3.1.1.5.3",  # linkDown
        "enterprise": "...",
        "varbinds": [
            {"oid": "1.3.6.1.2.1.2.2.1.1", "value": "3"},
            {"oid": "1.3.6.1.2.1.2.2.1.2.3", "value": "GigabitEthernet0/1"},
        ],
        "community": "public",
    }
    """
    # Well-known SNMP trap OIDs
    TRAP_TYPES = {
        "1.3.6.1.6.3.1.1.5.1": ("cold_start", "info", "Device Cold Start"),
        "1.3.6.1.6.3.1.1.5.2": ("warm_start", "info", "Device Warm Start"),
        "1.3.6.1.6.3.1.1.5.3": ("interface_down", "major", "Link Down"),
        "1.3.6.1.6.3.1.1.5.4": ("interface_up", "info", "Link Up"),
        "1.3.6.1.6.3.1.1.5.5": ("auth_failure", "warning", "Authentication Failure"),
    }

    oid = trap_data.get("oid", "")
    source_ip = trap_data.get("source_ip", "")
    varbinds = trap_data.get("varbinds", [])

    # Look up known trap type
    alert_type, severity, title = TRAP_TYPES.get(
        oid, ("snmp_trap", "info", f"SNMP Trap {oid}")
    )

    # Try to extract interface from varbinds
    interface = None
    for vb in varbinds:
        val = str(vb.get("value", ""))
        if re.match(r"(?:Gi|Te|Eth|xe-|ge-|et-)", val):
            interface = val
            break

    # Build description from varbinds
    varbind_str = "\n".join(
        f"  {vb.get('oid', '?')}: {vb.get('value', '?')}"
        for vb in varbinds
    )
    description = f"SNMP Trap from {source_ip}\nOID: {oid}\nVarbinds:\n{varbind_str}"

    normalized = {
        "source_type": "snmp",
        "severity": severity,
        "alert_type": alert_type,
        "title": f"{title} - {source_ip}",
        "description": description,
        "device_name": trap_data.get("hostname", source_ip),
        "device_ip": source_ip,
        "interface_name": interface,
        "raw_data": trap_data,
    }
    normalized["correlation_key"] = compute_correlation_key(normalized)
    return normalized


def normalize_webhook(
    payload: Dict[str, Any],
    source_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize a generic webhook payload into alert format.

    Uses best-effort field mapping for common webhook patterns.
    Works with PagerDuty, Datadog, Zabbix, custom systems, etc.
    """
    # Try many common field names
    title = (
        payload.get("title") or payload.get("summary") or
        payload.get("message") or payload.get("alert_name") or
        payload.get("name") or payload.get("subject") or
        "Webhook Alert"
    )

    description = (
        payload.get("description") or payload.get("details") or
        payload.get("body") or payload.get("text") or
        str(payload)
    )

    device_name = (
        payload.get("device") or payload.get("device_name") or
        payload.get("host") or payload.get("hostname") or
        payload.get("source") or ""
    )

    device_ip = (
        payload.get("device_ip") or payload.get("ip") or
        payload.get("ip_address") or payload.get("src_ip") or ""
    )

    interface = (
        payload.get("interface") or payload.get("interface_name") or
        payload.get("port") or None
    )

    severity_raw = (
        payload.get("severity") or payload.get("priority") or
        payload.get("level") or payload.get("urgency") or "info"
    )
    severity = _severity_from_string(str(severity_raw))

    alert_type = (
        payload.get("alert_type") or payload.get("event_type") or
        payload.get("type") or "webhook_alert"
    )

    normalized = {
        "source_type": "webhook",
        "source_name": source_hint,
        "severity": severity,
        "alert_type": alert_type,
        "title": str(title)[:500],
        "description": str(description)[:5000],
        "device_name": str(device_name),
        "device_ip": str(device_ip),
        "interface_name": interface,
        "raw_data": payload,
    }
    normalized["correlation_key"] = compute_correlation_key(normalized)
    return normalized
