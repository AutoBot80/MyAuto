-- Add year_of_mfg, cubic_capacity, body_type, seating_capacity, place_of_registeration to vehicle_master.
-- Run against database: auto_ai

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS year_of_mfg INTEGER,
ADD COLUMN IF NOT EXISTS cubic_capacity NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS body_type VARCHAR(16),
ADD COLUMN IF NOT EXISTS seating_capacity INTEGER,
ADD COLUMN IF NOT EXISTS place_of_registeration VARCHAR(32);

COMMENT ON COLUMN vehicle_master.year_of_mfg IS 'Year of manufacture (yyyy)';
COMMENT ON COLUMN vehicle_master.cubic_capacity IS 'Cubic capacity (cc)';
COMMENT ON COLUMN vehicle_master.body_type IS 'Body type (e.g. Sedan, SUV)';
COMMENT ON COLUMN vehicle_master.seating_capacity IS 'Seating capacity';
COMMENT ON COLUMN vehicle_master.place_of_registeration IS 'Place of registration';
