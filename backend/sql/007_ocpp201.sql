-- 007_ocpp201.sql
-- OCPP 2.0.1 dual-protocol support
-- Adds ocpp_version tracking and v201 transaction ID mapping

-- stations.ocpp_version: track which protocol version station uses
ALTER TABLE stations ADD COLUMN IF NOT EXISTS ocpp_version VARCHAR(10) DEFAULT '1.6';

-- Mapping v201 string transaction_id (UUID) -> numeric transaction_id (INTEGER)
-- Our ocpp_transactions table uses INTEGER transaction_id; v201 uses string UUIDs
CREATE TABLE IF NOT EXISTS ocpp_v201_tx_map (
    id SERIAL PRIMARY KEY,
    v201_transaction_id VARCHAR(36) NOT NULL,
    numeric_transaction_id INTEGER NOT NULL,
    station_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(v201_transaction_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_v201_tx_map_station ON ocpp_v201_tx_map(station_id);
CREATE INDEX IF NOT EXISTS idx_v201_tx_map_v201_id ON ocpp_v201_tx_map(v201_transaction_id);
