-- Link RTO queue rows to Add Sales staging (draft) handle.
-- Run after: 13a_add_sales_staging.sql, 10_rto_queue.sql (or 12c rename path).

ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS staging_id UUID;

DO $$
BEGIN
  IF to_regclass('public.add_sales_staging') IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conname = 'fk_rto_queue_staging'
    ) THEN
      ALTER TABLE rto_queue
        ADD CONSTRAINT fk_rto_queue_staging
        FOREIGN KEY (staging_id) REFERENCES add_sales_staging (staging_id);
    END IF;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_rto_queue_staging_id ON rto_queue (staging_id);

COMMENT ON COLUMN rto_queue.staging_id IS 'Add Sales staging UUID (add_sales_staging) for this sale when queued from Print Forms.';
