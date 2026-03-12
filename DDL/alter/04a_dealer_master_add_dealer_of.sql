-- Add dealer_of column to dealer_master (e.g. brand or company the dealer represents).
-- Run against database: auto_ai

ALTER TABLE dealer_master
ADD COLUMN IF NOT EXISTS dealer_of VARCHAR(255);

COMMENT ON COLUMN dealer_master.dealer_of IS 'Dealer of (e.g. brand or company name)';
