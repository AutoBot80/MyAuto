-- Add dms_link to oem_ref (URL for OEM DMS; used when opening DMS from app).
-- Run against database: auto_ai

ALTER TABLE oem_ref
  ADD COLUMN IF NOT EXISTS dms_link VARCHAR(512);

COMMENT ON COLUMN oem_ref.dms_link IS 'URL to OEM DMS (e.g. dummy or live); used when opening DMS from app';
