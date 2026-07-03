-- Per-sale add-on preset override (scoped to dealer prefer_insurer at fill time).
-- Run after: DDL/alter/37b_dealer_ref_insurance_addon.sql
-- Run against database: auto_ai

BEGIN;

ALTER TABLE add_sales_staging
  ADD COLUMN IF NOT EXISTS insurance_addon INTEGER;

ALTER TABLE add_sales_staging
  DROP CONSTRAINT IF EXISTS fk_add_sales_staging_insurance_addon;

ALTER TABLE add_sales_staging
  ADD CONSTRAINT fk_add_sales_staging_insurance_addon
  FOREIGN KEY (insurance_addon)
  REFERENCES insurance_addon_ref (insurance_addon_id);

COMMENT ON COLUMN add_sales_staging.insurance_addon IS
  'Per-sale add-on preset FK; copied from dealer_ref.insurance_addon on new INSERT; staging wins at fill';

-- No legacy backfill: NULL on existing rows resolves dealer default at fill time (same checkbox
-- outcome unless dealer default changes later). New INSERTs populate via application code.

COMMIT;
