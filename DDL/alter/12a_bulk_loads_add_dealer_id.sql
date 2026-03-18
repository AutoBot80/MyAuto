-- Add dealer_id to bulk_loads for multi-dealer support.
-- Run after: 12_bulk_loads.sql

ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS dealer_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_id ON bulk_loads (dealer_id);
