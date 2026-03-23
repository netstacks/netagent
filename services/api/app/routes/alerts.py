"""Alert ingestion and management routes for AI NOC pipeline."""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from netagent_core.db import get_db, Alert, AgentSession
from netagent_core.auth import get_current_user, get_current_user_optional, ALBUser
from netagent_core.utils import audit_log, AuditEventType
from netagent_core.alerts.normalizer import (
    normalize_syslog,
    normalize_splunk,
    normalize_snmp_trap,
    normalize_webhook,
    compute_correlation_key,
)
from netagent_core.redis_events import get_redis_client

logger = logging.getLogger(__name__)
router = APIRouter()

ALERT_DEDUP_TTL = 300  # 5 minutes dedup window


def _check_dedup(correlation_key: str) -> bool:
    """Check if alert is a duplicate. Returns True if new (not a dup)."""
    try:
        r = get_redis_client()
        key = f"alert:dedup:{correlation_key}"
        # SET NX returns True if key was set (new alert), False if already exists
        is_new = r.set(key, "1", nx=True, ex=ALERT_DEDUP_TTL)
        return bool(is_new)
    except Exception as e:
        logger.warning(f"Redis dedup check failed, allowing alert: {e}")
        return True


def _increment_correlation(db: Session, correlation_key: str) -> int:
    """Increment the correlation count for existing alerts with this key."""
    recent_cutoff = datetime.utcnow() - timedelta(seconds=ALERT_DEDUP_TTL)
    count = db.query(Alert).filter(
        Alert.correlation_key == correlation_key,
        Alert.received_at >= recent_cutoff,
        Alert.status.in_(["new", "triaging", "handed_off", "investigating"]),
    ).count()
    return count


def _create_alert_and_triage(db: Session, alert_data: dict) -> Alert:
    """Create alert record and queue triage task."""
    # Check dedup
    correlation_key = alert_data.get("correlation_key", "")
    is_new = _check_dedup(correlation_key)

    if not is_new:
        # Increment correlation count on the most recent matching alert
        existing = db.query(Alert).filter(
            Alert.correlation_key == correlation_key,
            Alert.status.in_(["new", "triaging", "handed_off", "investigating"]),
        ).order_by(Alert.received_at.desc()).first()

        if existing:
            existing.correlation_count += 1
            db.commit()
            return existing

    # Create new alert
    alert = Alert(
        source_type=alert_data.get("source_type", "generic"),
        source_name=alert_data.get("source_name"),
        severity=alert_data.get("severity", "info"),
        alert_type=alert_data.get("alert_type"),
        title=alert_data.get("title", "Alert"),
        description=alert_data.get("description"),
        device_name=alert_data.get("device_name"),
        device_ip=alert_data.get("device_ip"),
        interface_name=alert_data.get("interface_name"),
        raw_data=alert_data.get("raw_data"),
        correlation_key=correlation_key,
        occurred_at=datetime.fromisoformat(alert_data["occurred_at"]) if alert_data.get("occurred_at") else None,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    # Queue triage task via Celery
    try:
        from celery import current_app
        current_app.send_task(
            "tasks.alert_triage.triage_alert",
            args=[alert.id],
        )
        logger.info(f"Queued triage for alert {alert.id}: {alert.title}")
    except Exception as e:
        logger.warning(f"Failed to queue triage task (Celery may not be available): {e}")

    return alert


# =============================================================================
# INGEST ENDPOINTS
# =============================================================================


class GenericAlertIngest(BaseModel):
    source_type: str = "generic"
    source_name: Optional[str] = None
    severity: str = "info"
    alert_type: Optional[str] = None
    title: str
    description: Optional[str] = None
    device_name: Optional[str] = None
    device_ip: Optional[str] = None
    interface_name: Optional[str] = None
    raw_data: Optional[dict] = None


@router.post("/ingest")
async def ingest_alert(
    data: GenericAlertIngest,
    db: Session = Depends(get_db),
):
    """Ingest a pre-normalized alert."""
    alert_data = data.model_dump()
    alert_data["correlation_key"] = compute_correlation_key(alert_data)
    alert = _create_alert_and_triage(db, alert_data)
    return {"alert_id": alert.id, "status": alert.status, "is_duplicate": alert.correlation_count > 1}


@router.post("/ingest/syslog")
async def ingest_syslog(
    request: Request,
    db: Session = Depends(get_db),
):
    """Ingest a syslog message. Accepts JSON with raw, facility, severity, source_ip."""
    body = await request.json()
    alert_data = normalize_syslog(
        raw=body.get("raw", body.get("message", "")),
        facility=body.get("facility", 0),
        severity=body.get("severity", 6),
        source_ip=body.get("source_ip", ""),
    )
    alert_data["source_name"] = body.get("source_name")
    alert = _create_alert_and_triage(db, alert_data)
    return {"alert_id": alert.id, "status": alert.status}


@router.post("/ingest/splunk")
async def ingest_splunk(
    request: Request,
    db: Session = Depends(get_db),
):
    """Ingest a Splunk saved search webhook."""
    payload = await request.json()
    alert_data = normalize_splunk(payload)
    alert = _create_alert_and_triage(db, alert_data)
    return {"alert_id": alert.id, "status": alert.status}


@router.post("/ingest/snmp")
async def ingest_snmp(
    request: Request,
    db: Session = Depends(get_db),
):
    """Ingest an SNMP trap (from alert_listener service)."""
    trap_data = await request.json()
    alert_data = normalize_snmp_trap(trap_data)
    alert = _create_alert_and_triage(db, alert_data)
    return {"alert_id": alert.id, "status": alert.status}


@router.post("/ingest/webhook/{source_name}")
async def ingest_webhook(
    source_name: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Ingest a generic webhook alert from a named source."""
    payload = await request.json()
    alert_data = normalize_webhook(payload, source_hint=source_name)
    alert = _create_alert_and_triage(db, alert_data)
    return {"alert_id": alert.id, "status": alert.status}


# =============================================================================
# CRUD ENDPOINTS
# =============================================================================


@router.get("")
async def list_alerts(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    device_name: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    hours: int = Query(24, description="Look back N hours"),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """List alerts with filters."""
    query = db.query(Alert)

    if status:
        query = query.filter(Alert.status == status)
    if severity:
        query = query.filter(Alert.severity == severity)
    if device_name:
        query = query.filter(Alert.device_name.ilike(f"%{device_name}%"))
    if source_type:
        query = query.filter(Alert.source_type == source_type)

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    query = query.filter(Alert.received_at >= cutoff)

    total = query.count()
    alerts = query.order_by(Alert.received_at.desc()).offset(offset).limit(limit).all()

    items = []
    for a in alerts:
        items.append({
            "id": a.id,
            "source_type": a.source_type,
            "severity": a.severity,
            "alert_type": a.alert_type,
            "title": a.title,
            "device_name": a.device_name,
            "device_ip": a.device_ip,
            "interface_name": a.interface_name,
            "status": a.status,
            "correlation_count": a.correlation_count,
            "triage_session_id": a.triage_session_id,
            "handler_session_id": a.handler_session_id,
            "received_at": a.received_at.isoformat() if a.received_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        })

    return {"items": items, "total": total}


@router.get("/stats")
async def alert_stats(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    hours: int = Query(24),
):
    """Get alert statistics for dashboard."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Counts by severity
    severity_counts = dict(
        db.query(Alert.severity, func.count(Alert.id))
        .filter(Alert.received_at >= cutoff)
        .group_by(Alert.severity)
        .all()
    )

    # Counts by status
    status_counts = dict(
        db.query(Alert.status, func.count(Alert.id))
        .filter(Alert.received_at >= cutoff)
        .group_by(Alert.status)
        .all()
    )

    # Active (non-resolved) count
    active_count = db.query(Alert).filter(
        Alert.status.in_(["new", "triaging", "handed_off", "investigating"]),
        Alert.received_at >= cutoff,
    ).count()

    # Top devices with alerts
    top_devices = (
        db.query(Alert.device_name, func.count(Alert.id).label("count"))
        .filter(Alert.received_at >= cutoff, Alert.device_name.isnot(None))
        .group_by(Alert.device_name)
        .order_by(func.count(Alert.id).desc())
        .limit(10)
        .all()
    )

    return {
        "severity_counts": severity_counts,
        "status_counts": status_counts,
        "active_count": active_count,
        "total_count": sum(status_counts.values()),
        "top_devices": [{"device": d, "count": c} for d, c in top_devices],
        "hours": hours,
    }


@router.get("/{alert_id}")
async def get_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get alert details."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {
        "id": alert.id,
        "source_type": alert.source_type,
        "source_name": alert.source_name,
        "severity": alert.severity,
        "alert_type": alert.alert_type,
        "title": alert.title,
        "description": alert.description,
        "device_name": alert.device_name,
        "device_ip": alert.device_ip,
        "interface_name": alert.interface_name,
        "raw_data": alert.raw_data,
        "status": alert.status,
        "correlation_key": alert.correlation_key,
        "correlation_count": alert.correlation_count,
        "enrichment_data": alert.enrichment_data,
        "triage_session_id": alert.triage_session_id,
        "handler_session_id": alert.handler_session_id,
        "resolved_by": alert.resolved_by,
        "resolution_note": alert.resolution_note,
        "received_at": alert.received_at.isoformat() if alert.received_at else None,
        "occurred_at": alert.occurred_at.isoformat() if alert.occurred_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


class AlertUpdate(BaseModel):
    status: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None


@router.patch("/{alert_id}")
async def update_alert(
    alert_id: int,
    data: AlertUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update alert status or add resolution notes."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if data.status:
        alert.status = data.status
        if data.status == "resolved":
            alert.resolved_at = datetime.utcnow()
            alert.resolved_by = data.resolved_by or user.email

    if data.resolution_note:
        alert.resolution_note = data.resolution_note

    db.commit()

    audit_log(
        db,
        AuditEventType.SETTINGS_UPDATED,
        user=user,
        resource_type="alert",
        resource_id=alert.id,
        resource_name=alert.title,
        action="update",
        details=data.model_dump(exclude_unset=True),
    )

    return {"message": "Alert updated", "alert_id": alert.id, "status": alert.status}
