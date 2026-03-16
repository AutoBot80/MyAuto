-- Add Form 20 fields to vehicle_master (populated from DMS when available).
-- Run against database: auto_ai

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS oem_name VARCHAR(64),
ADD COLUMN IF NOT EXISTS vehicle_type VARCHAR(32),
ADD COLUMN IF NOT EXISTS num_cylinders INTEGER,
ADD COLUMN IF NOT EXISTS horse_power NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS length_mm INTEGER,
ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(16);

COMMENT ON COLUMN vehicle_master.oem_name IS 'OEM / Make (e.g. Hero MotoCorp)';
COMMENT ON COLUMN vehicle_master.vehicle_type IS 'Type of vehicle (e.g. LMV, 2W)';
COMMENT ON COLUMN vehicle_master.num_cylinders IS 'Number of cylinders';
COMMENT ON COLUMN vehicle_master.horse_power IS 'Horse power';
COMMENT ON COLUMN vehicle_master.length_mm IS 'Length in mm';
COMMENT ON COLUMN vehicle_master.fuel_type IS 'Fuel type (e.g. Petrol, Diesel)';
