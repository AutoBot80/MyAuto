-- Vehicle master data.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS vehicle_master (
    vehicle_id SERIAL PRIMARY KEY,
    key_num VARCHAR(32),
    engine VARCHAR(64),
    chassis VARCHAR(64),
    battery VARCHAR(64),
    plate_num VARCHAR(32),
    model VARCHAR(64),
    colour VARCHAR(64),
    raw_frame_num VARCHAR(32),
    raw_engine_num VARCHAR(32),
    raw_key_num VARCHAR(32),
    year_of_mfg INTEGER,
    cubic_capacity NUMERIC(10, 2),
    body_type VARCHAR(16),
    seating_capacity INTEGER,
    place_of_registeration VARCHAR(32),
    CONSTRAINT uq_vehicle_raw_triple UNIQUE (raw_frame_num, raw_engine_num, raw_key_num)
);

COMMENT ON TABLE vehicle_master IS 'Vehicle master; vehicle_id is auto-generated; upsert by (raw_frame_num, raw_engine_num, raw_key_num)';
