-- Unique on (raw_frame_num, raw_engine_num, raw_key_num) for upsert identity.
-- Run against database: auto_ai

ALTER TABLE vehicle_master
  ADD CONSTRAINT uq_vehicle_raw_triple UNIQUE (raw_frame_num, raw_engine_num, raw_key_num);
