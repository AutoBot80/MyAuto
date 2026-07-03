-- Document insurance_state=3 (GI complete: PDF + insurance_master INSERT).
-- No column change; comment only for operators / migrations audit.

COMMENT ON COLUMN add_sales_staging.insurance_state IS
  'Insurance processing state (0=not started; 2=policy preview Submit or portal manual issue — print resume; 3=GI complete — PDF + insurance_master INSERT)';
