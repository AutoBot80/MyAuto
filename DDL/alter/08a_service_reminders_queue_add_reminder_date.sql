-- Add reminder_date to service_reminders_queue (date when reminder should be sent).
-- Run against database: auto_ai

ALTER TABLE service_reminders_queue
  ADD COLUMN IF NOT EXISTS reminder_date DATE;

COMMENT ON COLUMN service_reminders_queue.reminder_date IS 'Date when this reminder should be sent (e.g. 15, 7, or 2 days before service_date)';
