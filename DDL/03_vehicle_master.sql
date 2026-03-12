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
    colour VARCHAR(64)
);

COMMENT ON TABLE vehicle_master IS 'Vehicle master; vehicle_id is auto-generated';
