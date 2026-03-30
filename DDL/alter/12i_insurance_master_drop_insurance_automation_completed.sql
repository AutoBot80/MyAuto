-- Remove insurance_automation_completed if a prior migration added it (replaced by policy_num gating on Add Sales).

ALTER TABLE insurance_master DROP COLUMN IF EXISTS insurance_automation_completed;
