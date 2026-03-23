"""Alert tools for agent situational awareness.

Allows agents to query recent alerts and update alert status,
giving them context about what's happening on the network.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Callable

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class AlertQueryTool:
    """Tool for querying recent network alerts."""

    name = "query_alerts"
    description = """Query recent network alerts to understand what's happening on the network.

Use this to:
- Check for related alerts on the same device
- See alert history and patterns (flapping, storms)
- Understand the current alert landscape before investigating
- Find correlated events across multiple devices

Returns a summary of matching alerts with severity, status, and timestamps."""

    requires_approval = False
    risk_level = "low"

    def __init__(self, db_session_factory: Callable):
        self.db_session_factory = db_session_factory

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Filter by device name (supports partial match)",
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "major", "minor", "warning", "info"],
                    "description": "Filter by severity level",
                },
                "status": {
                    "type": "string",
                    "enum": ["new", "triaging", "handed_off", "investigating", "resolved", "suppressed"],
                    "description": "Filter by alert status",
                },
                "alert_type": {
                    "type": "string",
                    "description": "Filter by alert type (e.g., interface_down, bgp_peer_down)",
                },
                "hours_back": {
                    "type": "integer",
                    "description": "Look back N hours (default: 4)",
                    "default": 4,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 20)",
                    "default": 20,
                },
            },
        }

    async def execute(
        self,
        device_name: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        alert_type: Optional[str] = None,
        hours_back: int = 4,
        limit: int = 20,
    ) -> str:
        from netagent_core.db import Alert

        with self.db_session_factory() as db:
            query = db.query(Alert)

            if device_name:
                query = query.filter(Alert.device_name.ilike(f"%{device_name}%"))
            if severity:
                query = query.filter(Alert.severity == severity)
            if status:
                query = query.filter(Alert.status == status)
            if alert_type:
                query = query.filter(Alert.alert_type == alert_type)

            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            query = query.filter(Alert.received_at >= cutoff)

            alerts = query.order_by(Alert.received_at.desc()).limit(limit).all()

            if not alerts:
                return f"No alerts found matching criteria in the last {hours_back} hours."

            lines = [f"Found {len(alerts)} alerts in the last {hours_back} hours:\n"]
            for a in alerts:
                age = datetime.utcnow() - a.received_at if a.received_at else timedelta(0)
                age_str = f"{int(age.total_seconds() // 60)}m ago"
                dup_str = f" (x{a.correlation_count})" if a.correlation_count > 1 else ""
                lines.append(
                    f"  [{a.severity.upper()}] {a.title}{dup_str}\n"
                    f"    Device: {a.device_name or 'N/A'} | Type: {a.alert_type or 'N/A'} | "
                    f"Status: {a.status} | {age_str} | ID: {a.id}"
                )

            return "\n".join(lines)


class AlertUpdateTool:
    """Tool for updating alert status and adding resolution notes."""

    name = "update_alert"
    description = """Update the status of a network alert or add resolution notes.

Use this to:
- Mark an alert as resolved after investigation
- Add notes about what was found or done
- Suppress duplicate/flapping alerts
- Mark an alert as being investigated"""

    requires_approval = False
    risk_level = "low"

    def __init__(self, db_session_factory: Callable):
        self.db_session_factory = db_session_factory

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "integer",
                    "description": "ID of the alert to update",
                },
                "status": {
                    "type": "string",
                    "enum": ["investigating", "resolved", "suppressed"],
                    "description": "New status for the alert",
                },
                "resolution_note": {
                    "type": "string",
                    "description": "Notes about the investigation or resolution",
                },
            },
            "required": ["alert_id", "status"],
        }

    async def execute(
        self,
        alert_id: int,
        status: str,
        resolution_note: Optional[str] = None,
    ) -> str:
        from netagent_core.db import Alert

        with self.db_session_factory() as db:
            alert = db.query(Alert).filter(Alert.id == alert_id).first()
            if not alert:
                return f"Alert {alert_id} not found."

            alert.status = status
            if resolution_note:
                alert.resolution_note = resolution_note
            if status == "resolved":
                alert.resolved_at = datetime.utcnow()
                alert.resolved_by = "ai_agent"

            db.commit()
            return f"Alert {alert_id} updated to '{status}'." + (f" Note: {resolution_note}" if resolution_note else "")


def create_alert_query_tool(db_session_factory: Callable) -> ToolDefinition:
    """Create an alert query tool instance."""
    tool = AlertQueryTool(db_session_factory)
    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=tool.execute,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
    )


def create_alert_update_tool(db_session_factory: Callable) -> ToolDefinition:
    """Create an alert update tool instance."""
    tool = AlertUpdateTool(db_session_factory)
    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=tool.execute,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
    )
