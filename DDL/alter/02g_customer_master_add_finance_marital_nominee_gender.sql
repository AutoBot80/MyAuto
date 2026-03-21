-- Add finance and marital/nominee customer attributes captured from Details sheet.
-- These values are stored on customer_master per current business requirement.

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS financier VARCHAR(255),
  ADD COLUMN IF NOT EXISTS marital_status VARCHAR(32),
  ADD COLUMN IF NOT EXISTS nominee_gender VARCHAR(16);

COMMENT ON COLUMN customer_master.financier IS 'Financier name captured from details sheet / insurance context';
COMMENT ON COLUMN customer_master.marital_status IS 'Customer marital status captured from details sheet';
COMMENT ON COLUMN customer_master.nominee_gender IS 'Nominee gender captured from details sheet';
