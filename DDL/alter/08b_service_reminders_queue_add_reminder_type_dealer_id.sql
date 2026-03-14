-- Add reminder_type and dealer_id to service_reminders_queue.
-- Run against database: auto_ai (after dealer_ref exists).

ALTER TABLE service_reminders_queue
  ADD COLUMN IF NOT EXISTS reminder_type VARCHAR(16),
  ADD COLUMN IF NOT EXISTS dealer_id INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_service_reminders_dealer' AND conrelid = 'service_reminders_queue'::regclass
  ) THEN
    ALTER TABLE service_reminders_queue
      ADD CONSTRAINT fk_service_reminders_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref(dealer_id);
  END IF;
END $$;

COMMENT ON COLUMN service_reminders_queue.reminder_type IS 'From oem_service_schedule (e.g. SMS)';
COMMENT ON COLUMN service_reminders_queue.dealer_id IS 'FK to dealer_ref';
