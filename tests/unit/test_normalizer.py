"""Tests for alert normalizer."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))

from netagent_core.alerts.normalizer import (
    normalize_syslog,
    normalize_splunk,
    normalize_snmp_trap,
    normalize_webhook,
    compute_correlation_key,
)


class TestComputeCorrelationKey:
    def test_same_device_and_type_produce_same_key(self):
        key1 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "interface_down"})
        key2 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "interface_down"})
        assert key1 == key2

    def test_different_device_produces_different_key(self):
        key1 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "interface_down"})
        key2 = compute_correlation_key({"device_name": "rtr-02", "alert_type": "interface_down"})
        assert key1 != key2

    def test_different_type_produces_different_key(self):
        key1 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "interface_down"})
        key2 = compute_correlation_key({"device_name": "rtr-01", "alert_type": "bgp_peer_down"})
        assert key1 != key2

    def test_missing_fields_use_unknown(self):
        key = compute_correlation_key({})
        assert key  # Should not crash


class TestNormalizeSyslog:
    def test_basic_syslog_message(self):
        result = normalize_syslog(
            raw="Interface GigabitEthernet0/1 is down",
            severity=3,
            source_ip="10.1.1.1",
        )
        assert result["source_type"] == "syslog"
        assert result["severity"] == "major"  # severity 3 = error = major
        assert result["alert_type"] == "interface_down"
        assert result["device_ip"] == "10.1.1.1"
        assert result["interface_name"] == "GigabitEthernet0/1"
        assert result["correlation_key"]

    def test_rfc3164_format(self):
        result = normalize_syslog(
            raw="<134>Jan  5 12:34:56 core-rtr-01 BGP peer 10.0.0.2 is down",
            severity=6,
            source_ip="10.1.1.1",
        )
        assert result["device_name"] == "core-rtr-01"
        assert result["alert_type"] == "bgp_peer_down"

    def test_interface_up_detection(self):
        result = normalize_syslog(
            raw="Link up on interface xe-0/0/1",
            severity=5,
            source_ip="10.1.1.1",
        )
        assert result["alert_type"] == "interface_up"
        assert result["interface_name"] == "xe-0/0/1"

    def test_high_cpu_detection(self):
        result = normalize_syslog(
            raw="CPU utilization exceeded threshold 90%",
            severity=4,
            source_ip="10.1.1.1",
        )
        assert result["alert_type"] == "high_cpu"
        assert result["severity"] == "warning"

    def test_severity_emergency(self):
        result = normalize_syslog(raw="System critical failure", severity=0, source_ip="10.1.1.1")
        assert result["severity"] == "critical"

    def test_unknown_message_type(self):
        result = normalize_syslog(raw="Something happened", severity=6, source_ip="10.1.1.1")
        assert result["alert_type"] == "generic_syslog"

    def test_raw_data_preserved(self):
        result = normalize_syslog(raw="test message", severity=6, source_ip="10.1.1.1")
        assert result["raw_data"]["raw_message"] == "test message"
        assert result["raw_data"]["source_ip"] == "10.1.1.1"


class TestNormalizeSplunk:
    def test_basic_splunk_webhook(self):
        payload = {
            "search_name": "Interface Down Alert",
            "app": "network_monitoring",
            "result": {
                "host": "edge-rtr-05",
                "severity": "high",
                "interface": "Gi0/0/1",
                "_raw": "Interface Gi0/0/1 went down at 12:00",
            },
        }
        result = normalize_splunk(payload)
        assert result["source_type"] == "splunk"
        assert result["device_name"] == "edge-rtr-05"
        assert result["severity"] == "critical"  # "high" maps to critical
        assert result["interface_name"] == "Gi0/0/1"
        assert "[Splunk]" in result["title"]

    def test_splunk_with_minimal_fields(self):
        payload = {"search_name": "Test Alert", "result": {}}
        result = normalize_splunk(payload)
        assert result["source_type"] == "splunk"
        assert result["severity"] == "info"

    def test_splunk_raw_data_preserved(self):
        payload = {"search_name": "Test", "result": {"host": "rtr-01"}}
        result = normalize_splunk(payload)
        assert result["raw_data"] == payload


class TestNormalizeSNMPTrap:
    def test_link_down_trap(self):
        trap = {
            "source_ip": "10.1.1.1",
            "oid": "1.3.6.1.6.3.1.1.5.3",
            "varbinds": [
                {"oid": "1.3.6.1.2.1.2.2.1.2.3", "value": "GigabitEthernet0/1"},
            ],
        }
        result = normalize_snmp_trap(trap)
        assert result["source_type"] == "snmp"
        assert result["alert_type"] == "interface_down"
        assert result["severity"] == "major"
        assert result["interface_name"] == "GigabitEthernet0/1"

    def test_link_up_trap(self):
        trap = {"source_ip": "10.1.1.1", "oid": "1.3.6.1.6.3.1.1.5.4", "varbinds": []}
        result = normalize_snmp_trap(trap)
        assert result["alert_type"] == "interface_up"
        assert result["severity"] == "info"

    def test_unknown_trap_oid(self):
        trap = {"source_ip": "10.1.1.1", "oid": "1.3.6.1.4.1.9999.1", "varbinds": []}
        result = normalize_snmp_trap(trap)
        assert result["alert_type"] == "snmp_trap"


class TestNormalizeWebhook:
    def test_basic_webhook(self):
        payload = {
            "title": "Interface Flapping",
            "severity": "warning",
            "device": "sw-core-01",
            "ip": "10.2.2.2",
            "interface": "Gi1/0/24",
        }
        result = normalize_webhook(payload)
        assert result["source_type"] == "webhook"
        assert result["title"] == "Interface Flapping"
        assert result["severity"] == "warning"
        assert result["device_name"] == "sw-core-01"
        assert result["device_ip"] == "10.2.2.2"
        assert result["interface_name"] == "Gi1/0/24"

    def test_webhook_with_source_hint(self):
        result = normalize_webhook({"title": "Test"}, source_hint="pagerduty")
        assert result["source_name"] == "pagerduty"

    def test_webhook_with_alternative_field_names(self):
        payload = {
            "summary": "High CPU on router",
            "priority": "medium",
            "hostname": "core-rtr-01",
            "src_ip": "10.3.3.3",
        }
        result = normalize_webhook(payload)
        assert result["title"] == "High CPU on router"
        assert result["severity"] == "major"  # "medium" maps to major
        assert result["device_name"] == "core-rtr-01"
        assert result["device_ip"] == "10.3.3.3"

    def test_webhook_empty_payload(self):
        result = normalize_webhook({})
        assert result["source_type"] == "webhook"
        assert result["severity"] == "info"
        assert result["title"] == "Webhook Alert"
