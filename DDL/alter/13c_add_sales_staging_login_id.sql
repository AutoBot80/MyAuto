-- Add Sales staging: who created/updated the draft (audit). Run after 13a_add_sales_staging.sql and login_ref (26/26b).

DO $$
BEGIN
  IF to_regclass('public.add_sales_staging') IS NULL THEN
    RAISE NOTICE 'add_sales_staging missing; skip 13c_add_sales_staging_login_id.';
    RETURN;
  END IF;
  IF to_regclass('public.login_ref') IS NULL THEN
    RAISE NOTICE 'login_ref missing; skip 13c_add_sales_staging_login_id.';
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'add_sales_staging' AND column_name = 'login_id'
  ) THEN
    -- Column-level REFERENCES cannot use "CONSTRAINT name FOREIGN KEY (...)"; use ADD COLUMN then ADD CONSTRAINT.
    ALTER TABLE add_sales_staging ADD COLUMN login_id VARCHAR(128) NULL;
    ALTER TABLE add_sales_staging
      ADD CONSTRAINT fk_add_sales_staging_login FOREIGN KEY (login_id) REFERENCES login_ref (login_id);
    CREATE INDEX IF NOT EXISTS idx_add_sales_staging_dealer_login
      ON add_sales_staging (dealer_id, login_id)
      WHERE login_id IS NOT NULL;
    COMMENT ON COLUMN add_sales_staging.login_id IS 'login_ref.login_id of the operator at Submit Info (draft insert/update).';
  END IF;
END $$;
