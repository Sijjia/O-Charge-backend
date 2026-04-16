-- OCPP Event Logs - P1 #10
-- Таблица для хранения OCPP событий (фильтрация из админки)

CREATE TABLE IF NOT EXISTS ocpp_event_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id VARCHAR(50),
    connector_id INTEGER,
    event_type VARCHAR(50) NOT NULL,
    direction VARCHAR(10) NOT NULL DEFAULT 'inbound',
    message_id VARCHAR(50),
    request_payload JSONB,
    response_payload JSONB,
    severity VARCHAR(20) DEFAULT 'info',
    processing_time_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для фильтрации из админки
CREATE INDEX IF NOT EXISTS idx_ocpp_logs_station ON ocpp_event_logs(station_id);
CREATE INDEX IF NOT EXISTS idx_ocpp_logs_type ON ocpp_event_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_ocpp_logs_created ON ocpp_event_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ocpp_logs_severity ON ocpp_event_logs(severity)
    WHERE severity IN ('error', 'critical');
