-- Migration: Add alerts table for AI NOC alert pipeline

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,
    source_name VARCHAR(255),
    severity VARCHAR(20) NOT NULL,
    alert_type VARCHAR(100),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    device_name VARCHAR(255),
    device_ip VARCHAR(50),
    interface_name VARCHAR(100),
    raw_data JSONB,
    status VARCHAR(30) DEFAULT 'new',
    triage_session_id INTEGER REFERENCES agent_sessions(id),
    handler_session_id INTEGER REFERENCES agent_sessions(id),
    correlation_key VARCHAR(255),
    correlation_count INTEGER DEFAULT 1,
    enrichment_data JSONB,
    resolved_by VARCHAR(255),
    resolution_note TEXT,
    received_at TIMESTAMP DEFAULT NOW(),
    occurred_at TIMESTAMP,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_source_type ON alerts(source_type);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_alert_type ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_device_name ON alerts(device_name);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_correlation_key ON alerts(correlation_key);
CREATE INDEX IF NOT EXISTS idx_alerts_received_at ON alerts(received_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status_received ON alerts(status, received_at);
CREATE INDEX IF NOT EXISTS idx_alerts_device_status ON alerts(device_name, status);
CREATE INDEX IF NOT EXISTS idx_alerts_severity_status ON alerts(severity, status);

COMMENT ON TABLE alerts IS 'Normalized network alert events for AI NOC triage pipeline';
