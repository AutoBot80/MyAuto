-- Add reminder_type to oem_service_schedule; set existing rows to 'SMS'.
-- Run against database: auto_ai

ALTER TABLE oem_service_schedule
  ADD COLUMN IF NOT EXISTS reminder_type VARCHAR(16);

UPDATE oem_service_schedule SET reminder_type = 'SMS' WHERE reminder_type IS NULL;

COMMENT ON COLUMN oem_service_schedule.reminder_type IS 'e.g. SMS, Email';
