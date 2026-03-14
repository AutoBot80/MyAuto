-- Add auto_sms_reminders (Y/N) to dealer_ref.
-- Run against database: auto_ai

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS auto_sms_reminders CHAR(1);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_dealer_ref_auto_sms_reminders' AND conrelid = 'dealer_ref'::regclass
  ) THEN
    ALTER TABLE dealer_ref
      ADD CONSTRAINT chk_dealer_ref_auto_sms_reminders CHECK (auto_sms_reminders IN ('Y', 'N'));
  END IF;
END $$;

COMMENT ON COLUMN dealer_ref.auto_sms_reminders IS 'Y or N';
