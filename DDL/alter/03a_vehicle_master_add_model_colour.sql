-- Add model and colour to vehicle_master.
-- Run against database: auto_ai

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS model VARCHAR(64),
ADD COLUMN IF NOT EXISTS colour VARCHAR(64);

COMMENT ON COLUMN vehicle_master.model IS 'Vehicle model';
COMMENT ON COLUMN vehicle_master.colour IS 'Vehicle colour';
