-- Add locked_by_login_id to rto_queue for multi-operator transparency.
-- Shows which user has locked the row when multiple operators process the same dealer.
-- Run after: 24a_rto_queue_schema_redesign.sql

ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS locked_by_login_id VARCHAR(128);

-- FK to login_ref (soft; login_ref may not exist in all envs)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'login_ref') THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
      WHERE conname = 'fk_rto_queue_locked_by_login'
        AND conrelid = 'rto_queue'::regclass
    ) THEN
      ALTER TABLE rto_queue ADD CONSTRAINT fk_rto_queue_locked_by_login
        FOREIGN KEY (locked_by_login_id) REFERENCES login_ref(login_id);
    END IF;
  END IF;
END $$;

COMMENT ON COLUMN rto_queue.locked_by_login_id IS 'login_id of operator who claimed this row for batch processing';
