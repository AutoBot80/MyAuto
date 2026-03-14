-- OEM reference: brand/manufacturer (e.g. Hero Motors). Run before dealer_ref.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS oem_ref (
    oem_id SERIAL PRIMARY KEY,
    oem_name VARCHAR(255),
    vehicles_type VARCHAR(128),
    dms_link VARCHAR(512)
);

COMMENT ON TABLE oem_ref IS 'OEM / brand reference; e.g. Hero Motors';
COMMENT ON COLUMN oem_ref.oem_name IS 'OEM or brand name';
COMMENT ON COLUMN oem_ref.vehicles_type IS 'Type of vehicles (e.g. 2W, 4W)';
COMMENT ON COLUMN oem_ref.dms_link IS 'URL to OEM DMS (e.g. dummy or live); used when opening DMS from app';
