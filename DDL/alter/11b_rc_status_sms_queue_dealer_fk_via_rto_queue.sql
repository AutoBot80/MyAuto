-- rc_status_sms_queue: ensure dealer_id matches rto_queue dealer_id (via sales_id).
-- Adds UNIQUE index on rto_queue(sales_id, dealer_id) to support the FK.
-- Run after: 12c_rename_rto_payment_details_to_rto_queue.sql and after rc_status_sms_queue has sales_id (11a).

-- 1) Ensure rto_queue supports FK target columns (sales_id unique already; add unique on (sales_id, dealer_id))
CREATE UNIQUE INDEX IF NOT EXISTS idx_rto_queue_sales_id_dealer_id
ON rto_queue (sales_id, dealer_id);

-- 2) Sync dealer_id from rto_queue
UPDATE rc_status_sms_queue rc
SET dealer_id = rq.dealer_id
FROM rto_queue rq
WHERE rc.sales_id = rq.sales_id
  AND (rc.dealer_id IS DISTINCT FROM rq.dealer_id);

-- 3) Replace dealer validation FK via sales_master with dealer validation via rto_queue
ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_sales_dealer;

ALTER TABLE rc_status_sms_queue
ADD CONSTRAINT fk_rc_rto_sales_dealer
FOREIGN KEY (sales_id, dealer_id)
REFERENCES rto_queue(sales_id, dealer_id);

COMMENT ON CONSTRAINT fk_rc_rto_sales_dealer ON rc_status_sms_queue
IS 'Ensures rc_status_sms_queue.dealer_id matches the dealer_id of the corresponding rto_queue row (via sales_id)';

