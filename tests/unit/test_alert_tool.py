"""Tests for alert query and update tools."""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))

from netagent_core.tools.alert_tool import AlertQueryTool, AlertUpdateTool


def _make_mock_alert(id=1, severity="major", title="Test Alert", device_name="rtr-01",
                     alert_type="interface_down", status="new", correlation_count=1):
    alert = MagicMock()
    alert.id = id
    alert.severity = severity
    alert.title = title
    alert.device_name = device_name
    alert.alert_type = alert_type
    alert.status = status
    alert.correlation_count = correlation_count
    alert.received_at = datetime.utcnow() - timedelta(minutes=5)
    return alert


@contextmanager
def _mock_db_factory(alerts=None, alert_for_update=None):
    """Create a mock db session factory."""
    db = MagicMock()

    if alerts is not None:
        query = db.query.return_value
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value.all.return_value = alerts

    if alert_for_update is not None:
        db.query.return_value.filter.return_value.first.return_value = alert_for_update

    @contextmanager
    def factory():
        yield db

    yield factory


class TestAlertQueryTool:
    @pytest.mark.asyncio
    async def test_query_returns_results(self):
        alerts = [_make_mock_alert(), _make_mock_alert(id=2, severity="critical", title="BGP Down")]
        with _mock_db_factory(alerts=alerts) as factory:
            tool = AlertQueryTool(factory)
            result = await tool.execute(device_name="rtr-01")

        assert "Found 2 alerts" in result
        assert "Test Alert" in result
        assert "BGP Down" in result

    @pytest.mark.asyncio
    async def test_query_no_results(self):
        with _mock_db_factory(alerts=[]) as factory:
            tool = AlertQueryTool(factory)
            result = await tool.execute(device_name="nonexistent")

        assert "No alerts found" in result

    @pytest.mark.asyncio
    async def test_query_shows_correlation_count(self):
        alerts = [_make_mock_alert(correlation_count=5)]
        with _mock_db_factory(alerts=alerts) as factory:
            tool = AlertQueryTool(factory)
            result = await tool.execute()

        assert "x5" in result

    def test_parameters_schema(self):
        with _mock_db_factory() as factory:
            tool = AlertQueryTool(factory)
            params = tool.parameters
            assert "device_name" in params["properties"]
            assert "severity" in params["properties"]
            assert "status" in params["properties"]


class TestAlertUpdateTool:
    @pytest.mark.asyncio
    async def test_update_status(self):
        alert = _make_mock_alert()
        with _mock_db_factory(alert_for_update=alert) as factory:
            tool = AlertUpdateTool(factory)
            result = await tool.execute(alert_id=1, status="resolved", resolution_note="Fixed")

        assert "updated to 'resolved'" in result
        assert alert.status == "resolved"
        assert alert.resolution_note == "Fixed"

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        db = MagicMock()
        # Make the query chain return None for .first()
        db.query.return_value.filter.return_value.first.return_value = None

        @contextmanager
        def factory():
            yield db

        tool = AlertUpdateTool(factory)
        result = await tool.execute(alert_id=999, status="resolved")

        assert "not found" in result
