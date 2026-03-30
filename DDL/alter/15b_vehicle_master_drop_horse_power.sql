-- Drop horse_power: no longer scraped or persisted from DMS (Form 19 left blank in templates).
ALTER TABLE vehicle_master DROP COLUMN IF EXISTS horse_power;
