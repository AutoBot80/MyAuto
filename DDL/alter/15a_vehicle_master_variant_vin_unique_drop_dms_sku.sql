-- vehicle_master: variant column; unique VIN (chassis) when populated; drop dms_sku.
-- Run against database: auto_ai
--
-- Fails if existing rows have duplicate non-empty chassis (case-insensitive after trim).
-- Resolve duplicates before applying.

ALTER TABLE vehicle_master
    ADD COLUMN IF NOT EXISTS variant VARCHAR(64);

COMMENT ON COLUMN vehicle_master.variant IS 'Vehicle variant from Siebel Vehicles page scrape';

ALTER TABLE vehicle_master
    ALTER COLUMN place_of_registeration TYPE VARCHAR(128);

DROP INDEX IF EXISTS uq_vehicle_master_chassis_nonempty;

CREATE UNIQUE INDEX uq_vehicle_master_chassis_nonempty
    ON vehicle_master (UPPER(BTRIM(chassis)))
    WHERE chassis IS NOT NULL AND BTRIM(chassis) <> '';

ALTER TABLE vehicle_master DROP COLUMN IF EXISTS dms_sku;
