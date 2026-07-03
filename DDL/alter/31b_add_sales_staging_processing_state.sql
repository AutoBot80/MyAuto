-- add_sales_staging: integer processing-state flags for DMS and insurance automation.
-- See Documentation/Database DDL.md add_sales_staging.

ALTER TABLE add_sales_staging ADD COLUMN IF NOT EXISTS dms_state INTEGER NOT NULL DEFAULT 0;
ALTER TABLE add_sales_staging ADD COLUMN IF NOT EXISTS insurance_state INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN add_sales_staging.dms_state IS 'DMS processing state (0=not started; reserved for future steps)';
COMMENT ON COLUMN add_sales_staging.insurance_state IS 'Insurance processing state (0=not started; 2=policy preview Submit clicked on MISP)';
