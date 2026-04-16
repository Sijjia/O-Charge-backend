-- 004_bookings.sql
-- Booking API — Бронирование коннекторов
-- Red Petroleum EV — PRD modules/booking/PRD.md

CREATE TABLE bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    station_id VARCHAR(50) NOT NULL,
    connector_id INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'active',  -- active, used, expired, cancelled
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    session_id UUID
);

CREATE INDEX idx_bookings_user ON bookings(user_id);
CREATE INDEX idx_bookings_station ON bookings(station_id, connector_id);
CREATE INDEX idx_bookings_status ON bookings(status);
CREATE INDEX idx_bookings_expires ON bookings(expires_at);

-- Один активный booking на коннектор
CREATE UNIQUE INDEX idx_bookings_active_connector
ON bookings(station_id, connector_id) WHERE status = 'active';
