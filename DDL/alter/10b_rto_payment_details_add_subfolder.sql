-- Add subfolder to rto_payment_details for saving RTO Payment Proof screenshot.
ALTER TABLE rto_payment_details ADD COLUMN IF NOT EXISTS subfolder VARCHAR(128);
