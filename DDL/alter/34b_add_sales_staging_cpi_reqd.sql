-- Snapshot CPI flag on Add Sales staging rows (derived from dealer_ref.cpi_reqd on new inserts).
-- Backfill legacy rows to Y; no DEFAULT so application must supply on new INSERT.
-- Run after: 34a_dealer_ref_cpi_reqd.sql, 13a_add_sales_staging.sql
-- Run against database: auto_ai

BEGIN;

ALTER TABLE add_sales_staging
  ADD COLUMN IF NOT EXISTS cpi_reqd CHAR(1);

UPDATE add_sales_staging
SET cpi_reqd = 'Y'
WHERE cpi_reqd IS NULL;

ALTER TABLE add_sales_staging
  ALTER COLUMN cpi_reqd SET NOT NULL;

ALTER TABLE add_sales_staging
  DROP CONSTRAINT IF EXISTS chk_add_sales_staging_cpi_reqd;

ALTER TABLE add_sales_staging
  ADD CONSTRAINT chk_add_sales_staging_cpi_reqd CHECK (cpi_reqd IN ('Y', 'N'));

COMMENT ON COLUMN add_sales_staging.cpi_reqd IS
  'Y or N: CPI required for this staging snapshot; legacy rows backfilled Y; new rows from dealer_ref.cpi_reqd at insert';

COMMIT;
