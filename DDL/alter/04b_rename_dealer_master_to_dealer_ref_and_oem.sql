-- Rename dealer_master to dealer_ref; replace dealer_of with oem_id (FK to oem_ref).
-- Run against database: auto_ai (after oem_ref exists).

-- 1) Create oem_ref if not exists
CREATE TABLE IF NOT EXISTS oem_ref (
    oem_id SERIAL PRIMARY KEY,
    oem_name VARCHAR(255),
    vehicles_type VARCHAR(128)
);

-- 2) Migrate distinct dealer_of values into oem_ref (skip if already migrated)
INSERT INTO oem_ref (oem_name)
SELECT DISTINCT TRIM(d.dealer_of) FROM dealer_master d
WHERE d.dealer_of IS NOT NULL AND TRIM(d.dealer_of) <> ''
  AND NOT EXISTS (SELECT 1 FROM oem_ref o WHERE o.oem_name = TRIM(d.dealer_of));

-- 3) Add oem_id to dealer_master
ALTER TABLE dealer_master ADD COLUMN IF NOT EXISTS oem_id INTEGER;

-- 4) Set oem_id from matching oem_ref.oem_name
UPDATE dealer_master d
SET oem_id = (SELECT o.oem_id FROM oem_ref o WHERE o.oem_name = TRIM(d.dealer_of) LIMIT 1)
WHERE d.dealer_of IS NOT NULL AND TRIM(d.dealer_of) <> '';

-- 5) Drop old column and add FK
ALTER TABLE dealer_master DROP COLUMN IF EXISTS dealer_of;
ALTER TABLE dealer_master
  ADD CONSTRAINT fk_dealer_ref_oem FOREIGN KEY (oem_id) REFERENCES oem_ref(oem_id);

-- 6) Rename table
ALTER TABLE dealer_master RENAME TO dealer_ref;

COMMENT ON TABLE dealer_ref IS 'Dealer reference; parent_id for hierarchy; oem_id references oem_ref';
COMMENT ON COLUMN dealer_ref.oem_id IS 'FK to oem_ref (OEM/brand); supplied on insert, not auto-generated';
