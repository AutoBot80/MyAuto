-- Drop redundant insurance_cost column; premium is the single monetary column (preview + staging).
-- Run after 14b_insurance_master_add_insurance_cost.sql if that was applied.
-- Application: insert_insurance_master_after_gi / update_insurance_master_policy_after_issue use premium only.

ALTER TABLE insurance_master
    DROP COLUMN IF EXISTS insurance_cost;
