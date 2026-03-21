-- Add RTO_Name to dealer_ref and set default operational value for Arya dealer row.

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS rto_name VARCHAR(128);

UPDATE dealer_ref
SET rto_name = 'RTO-Bharatpur'
WHERE dealer_id = 100001;

COMMENT ON COLUMN dealer_ref.rto_name IS 'Dealer-mapped RTO office name (e.g. RTO-Bharatpur)';
