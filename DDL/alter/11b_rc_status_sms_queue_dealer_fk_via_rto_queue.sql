-- rc_status_sms_queue: dealer_id validated via sales_master (sales_id, dealer_id).
-- Run after: 24a_rto_queue_schema_redesign.sql (old rto_queue columns dropped).

-- Drop old FKs that referenced rto_queue columns no longer present
ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_rto;
ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_rto_sales_dealer;
DROP INDEX IF EXISTS idx_rto_queue_sales_id_dealer_id;

-- Ensure sales_master has unique (sales_id, dealer_id) for compound FK
CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_master_sales_id_dealer_id
ON sales_master (sales_id, dealer_id);

-- Add dealer validation FK via sales_master
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_rc_sales_dealer' AND conrelid = 'rc_status_sms_queue'::regclass) THEN
    ALTER TABLE rc_status_sms_queue
    ADD CONSTRAINT fk_rc_sales_dealer
    FOREIGN KEY (sales_id, dealer_id)
    REFERENCES sales_master(sales_id, dealer_id);
  END IF;
END $$;

COMMENT ON CONSTRAINT fk_rc_sales_dealer ON rc_status_sms_queue
IS 'Ensures rc_status_sms_queue.dealer_id matches the dealer_id of the corresponding sales_master row';
