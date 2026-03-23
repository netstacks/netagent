"""Integration tests for alert API endpoints."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api', 'app'))

# Mock Redis before importing routes
with patch("redis.from_url"), \
     patch("netagent_core.redis_events.get_redis_client") as mock_redis:
    mock_redis_client = MagicMock()
    mock_redis_client.set.return_value = True  # New alert (not duplicate)
    mock_redis.return_value = mock_redis_client

    from fastapi.testclient import TestClient
    from netagent_core.db import Base


@pytest.fixture
def mock_db():
    """Mock database session with alert support."""
    db = MagicMock()

    # Mock alert creation
    def add_side_effect(obj):
        obj.id = 1
        obj.status = getattr(obj, 'status', 'new')
        obj.correlation_count = getattr(obj, 'correlation_count', 1)
        obj.received_at = datetime.utcnow()

    db.add.side_effect = add_side_effect
    db.commit.return_value = None
    db.refresh = lambda x: None
    return db


class TestAlertNormalization:
    """Test that different alert formats are properly normalized."""

    def test_syslog_normalization(self):
        from netagent_core.alerts.normalizer import normalize_syslog

        result = normalize_syslog(
            raw="<131>Jan 10 08:15:23 core-rtr-01 Interface GigabitEthernet0/1 is down",
            facility=16,
            severity=3,
            source_ip="10.1.1.1",
        )
        assert result["source_type"] == "syslog"
        assert result["alert_type"] == "interface_down"
        assert result["device_name"] == "core-rtr-01"
        assert result["correlation_key"]

    def test_splunk_normalization(self):
        from netagent_core.alerts.normalizer import normalize_splunk

        result = normalize_splunk({
            "search_name": "BGP Peer Down",
            "app": "network",
            "result": {
                "host": "border-rtr-01",
                "severity": "critical",
                "event_type": "bgp_peer_down",
                "_raw": "BGP-5-ADJCHANGE: neighbor 10.0.0.1 Down",
            },
        })
        assert result["source_type"] == "splunk"
        assert result["severity"] == "critical"
        assert result["device_name"] == "border-rtr-01"

    def test_webhook_normalization(self):
        from netagent_core.alerts.normalizer import normalize_webhook

        result = normalize_webhook({
            "title": "High CPU Usage",
            "severity": "warning",
            "device": "sw-01",
            "ip": "10.2.2.2",
        }, source_hint="monitoring_system")

        assert result["source_type"] == "webhook"
        assert result["source_name"] == "monitoring_system"
        assert result["severity"] == "warning"
        assert result["device_name"] == "sw-01"


class TestAlertDedup:
    """Test alert deduplication logic."""

    def test_correlation_key_consistency(self):
        from netagent_core.alerts.normalizer import compute_correlation_key

        # Same alert data should produce the same key
        data = {"device_name": "rtr-01", "alert_type": "interface_down"}
        key1 = compute_correlation_key(data)
        key2 = compute_correlation_key(data)
        assert key1 == key2

    def test_different_alerts_different_keys(self):
        from netagent_core.alerts.normalizer import compute_correlation_key

        key1 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "interface_down"})
        key2 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "bgp_peer_down"})
        assert key1 != key2


class TestAlertIngestionFlow:
    """Test the full alert ingestion flow."""

    def test_syslog_produces_valid_alert_data(self):
        from netagent_core.alerts.normalizer import normalize_syslog

        result = normalize_syslog(
            raw="OSPF neighbor 10.0.0.3 on interface GigabitEthernet0/2 is dead",
            severity=3,
            source_ip="10.1.1.1",
        )

        # Verify all required fields are present
        assert "source_type" in result
        assert "severity" in result
        assert "alert_type" in result
        assert "title" in result
        assert "correlation_key" in result
        assert "raw_data" in result

        # Verify classification
        assert result["alert_type"] == "ospf_neighbor_down"
        assert result["interface_name"] == "GigabitEthernet0/2"
