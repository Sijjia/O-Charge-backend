-- 100_seed_connector_sessions.sql
-- Seed OCPP transactions, meter values, station status, and ocpp_ws_url
-- Depends on: 099_demo_data.sql (locations, stations, connectors, sessions)
-- Run on VPS: psql -U evpower -d evpower -f 100_seed_connector_sessions.sql

-- ============================================
-- 0. Add ocpp_ws_url column if not exists
-- ============================================
ALTER TABLE stations ADD COLUMN IF NOT EXISTS ocpp_ws_url VARCHAR(500);

-- ============================================
-- 1. Clean up old OCPP demo data
-- ============================================
DELETE FROM ocpp_meter_values WHERE station_id LIKE 'st-%';
DELETE FROM ocpp_transactions WHERE station_id LIKE 'st-%';
DELETE FROM ocpp_station_status WHERE station_id LIKE 'st-%';

-- ============================================
-- 2. Fix connector_id in existing sessions
--    (was all connector_id=1, now randomize to real connectors)
-- ============================================
UPDATE charging_sessions cs SET connector_id = sub.cn
FROM (
    SELECT cs2.id AS session_id,
           (SELECT c.connector_number FROM connectors c
            WHERE c.station_id = cs2.station_id
            ORDER BY random() LIMIT 1) AS cn
    FROM charging_sessions cs2
    WHERE cs2.station_id LIKE 'st-%'
) sub
WHERE cs.id = sub.session_id AND sub.cn IS NOT NULL;

-- ============================================
-- 3. OCPP transactions for each session (~500)
-- ============================================
DO $$
DECLARE
    v_rec RECORD;
    v_tx_id INT := 1000;
    v_meter_start DECIMAL;
    v_meter_stop DECIMAL;
BEGIN
    FOR v_rec IN
        SELECT id, station_id, connector_id, user_id,
               start_time, stop_time, energy
        FROM charging_sessions
        WHERE station_id LIKE 'st-%' AND status = 'stopped'
        ORDER BY start_time
    LOOP
        v_tx_id := v_tx_id + 1;
        v_meter_start := ROUND((random() * 50000)::numeric, 2);
        v_meter_stop := v_meter_start + COALESCE(v_rec.energy, 10.0);

        INSERT INTO ocpp_transactions (
            transaction_id, station_id, connector_id, id_tag,
            meter_start, meter_stop, start_timestamp, stop_timestamp,
            stop_reason, charging_session_id, status
        ) VALUES (
            v_tx_id,
            v_rec.station_id,
            COALESCE(v_rec.connector_id, 1),
            COALESCE(v_rec.user_id, 'anonymous'),
            v_meter_start,
            v_meter_stop,
            v_rec.start_time,
            v_rec.stop_time,
            CASE floor(random() * 5)::int
                WHEN 0 THEN 'EVDisconnected'
                WHEN 1 THEN 'Remote'
                WHEN 2 THEN 'Local'
                ELSE 'EVDisconnected'
            END,
            v_rec.id,
            'Stopped'
        );
    END LOOP;

    RAISE NOTICE 'Created % OCPP transactions', v_tx_id - 1000;
END $$;

-- ============================================
-- 4. OCPP meter values (3-8 per transaction)
-- ============================================
DO $$
DECLARE
    v_tx RECORD;
    v_steps INT;
    v_step_duration INTERVAL;
    v_ts TIMESTAMPTZ;
    v_energy_step DECIMAL;
    v_current_energy DECIMAL;
    v_power DECIMAL;
    v_mv_count INT := 0;
BEGIN
    FOR v_tx IN
        SELECT ot.id AS ocpp_id, ot.transaction_id, ot.station_id, ot.connector_id,
               ot.meter_start, ot.meter_stop, ot.start_timestamp, ot.stop_timestamp
        FROM ocpp_transactions ot
        WHERE ot.station_id LIKE 'st-%' AND ot.stop_timestamp IS NOT NULL
    LOOP
        -- Random 3-8 meter values per transaction
        v_steps := 3 + floor(random() * 6)::int;
        v_step_duration := (v_tx.stop_timestamp - v_tx.start_timestamp) / v_steps;
        v_energy_step := COALESCE((v_tx.meter_stop - v_tx.meter_start) / v_steps, 2.0);
        v_current_energy := v_tx.meter_start;

        -- Get station power for realistic power readings
        SELECT COALESCE(s.max_power, 22.0) INTO v_power
        FROM stations s WHERE s.id = v_tx.station_id;

        FOR i IN 1..v_steps LOOP
            v_ts := v_tx.start_timestamp + (v_step_duration * i);
            v_current_energy := v_current_energy + v_energy_step;
            v_mv_count := v_mv_count + 1;

            INSERT INTO ocpp_meter_values (
                transaction_id, ocpp_transaction_id, station_id, connector_id,
                timestamp, sampled_values,
                energy_active_import_register, power_active_import,
                current_import, voltage, soc
            ) VALUES (
                v_tx.transaction_id,
                v_tx.ocpp_id,
                v_tx.station_id,
                v_tx.connector_id,
                v_ts,
                jsonb_build_array(
                    jsonb_build_object(
                        'value', ROUND(v_current_energy::numeric, 2)::text,
                        'measurand', 'Energy.Active.Import.Register',
                        'unit', 'Wh'
                    ),
                    jsonb_build_object(
                        'value', ROUND((v_power * (0.5 + random() * 0.5) * 1000)::numeric, 0)::text,
                        'measurand', 'Power.Active.Import',
                        'unit', 'W'
                    ),
                    jsonb_build_object(
                        'value', ROUND((220 + random() * 20)::numeric, 1)::text,
                        'measurand', 'Voltage',
                        'unit', 'V'
                    )
                ),
                ROUND(v_current_energy::numeric, 2),
                ROUND((v_power * (0.5 + random() * 0.5))::numeric, 2),
                ROUND((v_power * 1000 / 230 * (0.5 + random() * 0.5))::numeric, 1),
                ROUND((220 + random() * 20)::numeric, 1),
                CASE WHEN i < v_steps
                    THEN ROUND((20 + (80.0 * i / v_steps))::numeric, 0)
                    ELSE ROUND((85 + random() * 15)::numeric, 0)
                END
            );
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Created % OCPP meter values', v_mv_count;
END $$;

-- ============================================
-- 5. OCPP station status (~70% online)
-- ============================================
INSERT INTO ocpp_station_status (station_id, status, is_online, last_heartbeat, connector_status)
SELECT
    s.id,
    CASE WHEN random() < 0.70 THEN 'Available' ELSE 'Unavailable' END,
    random() < 0.70,
    NOW() - (floor(random() * 30) || ' minutes')::interval,
    (
        SELECT jsonb_agg(jsonb_build_object(
            'connector_id', c.connector_number,
            'status', CASE
                WHEN random() < 0.6 THEN 'Available'
                WHEN random() < 0.8 THEN 'Charging'
                WHEN random() < 0.9 THEN 'Preparing'
                ELSE 'Finishing'
            END,
            'error_code', 'NoError'
        ) ORDER BY c.connector_number)
        FROM connectors c WHERE c.station_id = s.id
    )
FROM stations s
WHERE s.id LIKE 'st-%'
ON CONFLICT (station_id) DO UPDATE SET
    status = EXCLUDED.status,
    is_online = EXCLUDED.is_online,
    last_heartbeat = EXCLUDED.last_heartbeat,
    connector_status = EXCLUDED.connector_status,
    updated_at = NOW();

-- ============================================
-- 6. Set default ocpp_ws_url for all stations
-- ============================================
UPDATE stations SET ocpp_ws_url = 'wss://ocpp.charge.redpay.kg/ws/' || id
WHERE ocpp_ws_url IS NULL AND id LIKE 'st-%';

-- ============================================
-- 7. Sync stations.last_heartbeat_at + is_available
--    ~80% online (heartbeat within last 1 min — fresh)
--    ~20% offline (heartbeat 30-120 min ago)
-- ============================================
-- Step A: Set ~80% of active stations to online
UPDATE stations SET
    is_available = true,
    last_heartbeat_at = NOW() - (floor(random() * 1) || ' minutes')::interval
WHERE id LIKE 'st-%' AND status = 'active'
  AND id IN (
    SELECT id FROM stations
    WHERE id LIKE 'st-%' AND status = 'active'
    ORDER BY random()
    LIMIT (SELECT CEIL(COUNT(*) * 0.80) FROM stations WHERE id LIKE 'st-%' AND status = 'active')
  );

-- Step B: The rest stay offline
UPDATE stations SET
    is_available = false,
    last_heartbeat_at = NOW() - ((30 + floor(random() * 90)) || ' minutes')::interval
WHERE id LIKE 'st-%' AND (is_available = false OR status != 'active');

-- ============================================
-- 8. Randomize connector statuses
--    ~60% available, ~20% occupied, ~10% charging,
--    ~5% preparing, ~5% faulted
-- ============================================
UPDATE connectors SET status = CASE
    WHEN random() < 0.60 THEN 'available'
    WHEN random() < 0.80 THEN 'occupied'
    WHEN random() < 0.90 THEN 'occupied'
    WHEN random() < 0.95 THEN 'available'
    ELSE 'faulted'
END
WHERE station_id LIKE 'st-%';

-- Force connectors on offline stations to be unavailable
UPDATE connectors c SET status = 'faulted'
FROM stations s
WHERE c.station_id = s.id
  AND s.id LIKE 'st-%'
  AND (s.is_available = false OR s.last_heartbeat_at < NOW() - INTERVAL '5 minutes');

-- ============================================
-- Summary
-- ============================================
DO $$
DECLARE v_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO v_count FROM ocpp_transactions WHERE station_id LIKE 'st-%';
    RAISE NOTICE 'OCPP transactions: %', v_count;
    SELECT COUNT(*) INTO v_count FROM ocpp_meter_values WHERE station_id LIKE 'st-%';
    RAISE NOTICE 'OCPP meter values: %', v_count;
    SELECT COUNT(*) INTO v_count FROM ocpp_station_status WHERE station_id LIKE 'st-%';
    RAISE NOTICE 'OCPP station status: %', v_count;
    SELECT COUNT(*) INTO v_count FROM stations WHERE ocpp_ws_url IS NOT NULL AND id LIKE 'st-%';
    RAISE NOTICE 'Stations with ocpp_ws_url: %', v_count;
    SELECT COUNT(*) INTO v_count FROM stations WHERE is_available = true AND id LIKE 'st-%';
    RAISE NOTICE 'Stations online (is_available=true): %', v_count;
    SELECT COUNT(*) INTO v_count FROM stations
        WHERE last_heartbeat_at > NOW() - INTERVAL '5 minutes' AND id LIKE 'st-%';
    RAISE NOTICE 'Stations with recent heartbeat (<5min): %', v_count;
    SELECT COUNT(*) INTO v_count FROM connectors
        WHERE status = 'available' AND station_id LIKE 'st-%';
    RAISE NOTICE 'Connectors available: %', v_count;
    SELECT COUNT(*) INTO v_count FROM connectors
        WHERE status = 'occupied' AND station_id LIKE 'st-%';
    RAISE NOTICE 'Connectors occupied: %', v_count;
END $$;
