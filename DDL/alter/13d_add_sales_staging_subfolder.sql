-- Canonical upload/OCR subfolder for Add Sales (mirrors payload_json.file_location). Run after 13a.

DO $$
BEGIN
  IF to_regclass('public.add_sales_staging') IS NULL THEN
    RAISE NOTICE 'add_sales_staging missing; skip 13d_add_sales_staging_subfolder.';
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'add_sales_staging' AND column_name = 'subfolder'
  ) THEN
    ALTER TABLE add_sales_staging ADD COLUMN subfolder VARCHAR(256) NULL;
    UPDATE add_sales_staging
    SET subfolder = NULLIF(TRIM(payload_json->>'file_location'), '')
    WHERE subfolder IS NULL
      AND payload_json ? 'file_location';
    COMMENT ON COLUMN add_sales_staging.subfolder IS 'Upload scans subfolder (same as payload file_location); used when API sends staging_id only.';
  END IF;
END $$;
