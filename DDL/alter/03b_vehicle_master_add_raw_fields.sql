-- Add raw OCR fields to vehicle_master.
-- Run against database: auto_ai

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS raw_frame_num VARCHAR(32),
ADD COLUMN IF NOT EXISTS raw_engine_num VARCHAR(32),
ADD COLUMN IF NOT EXISTS raw_key_num VARCHAR(32);

COMMENT ON COLUMN vehicle_master.raw_frame_num IS 'Raw extracted frame/chassis number as-read from source';
COMMENT ON COLUMN vehicle_master.raw_engine_num IS 'Raw extracted engine number as-read from source';
COMMENT ON COLUMN vehicle_master.raw_key_num IS 'Raw extracted key number as-read from source';
