-- Per-row opt-in for Fill Vahan batch (default checked in UI).
ALTER TABLE rto_queue
    ADD COLUMN IF NOT EXISTS in_queue BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN rto_queue.in_queue IS 'When true, row is eligible for Fill Vahan Site batch claim.';
