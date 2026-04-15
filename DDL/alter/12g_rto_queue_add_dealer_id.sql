-- Denormalized dealer on rto_queue for filtering and API payloads (must match sales_master.dealer_id).
-- Run after: rto_queue exists, sales_master, dealer_ref.

ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS dealer_id INTEGER;

UPDATE rto_queue rq
SET dealer_id = sm.dealer_id
FROM sales_master sm
WHERE sm.sales_id = rq.sales_id
  AND (rq.dealer_id IS NULL OR rq.dealer_id IS DISTINCT FROM sm.dealer_id);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_rto_queue_dealer') THEN
    ALTER TABLE rto_queue
      ADD CONSTRAINT fk_rto_queue_dealer
      FOREIGN KEY (dealer_id) REFERENCES dealer_ref (dealer_id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_rto_queue_dealer_id ON rto_queue (dealer_id);

COMMENT ON COLUMN rto_queue.dealer_id IS 'Dealer for this queue row; must match sales_master.dealer_id for sales_id.';
