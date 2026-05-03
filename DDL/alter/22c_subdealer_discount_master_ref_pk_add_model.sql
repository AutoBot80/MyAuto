-- subdealer_discount_master_ref: extend primary key to include model:
--   (dealer_id, subdealer_type, valid_flag) -> (dealer_id, subdealer_type, valid_flag, model)
-- so multiple rows can share the same (dealer, subdealer_type, valid) with different models.
-- No-op if the primary key is already 4 columns (e.g. greenfield DDL/22 after this change).
-- Run after alter/22b (or on any table that still has the 3-column composite PK only).
-- Run against database: auto_ai

DO $m$
DECLARE
  pk_col_count int;
BEGIN
  SELECT count(*)::int
  INTO pk_col_count
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage k
    ON k.constraint_schema = tc.constraint_schema
   AND k.constraint_name = tc.constraint_name
   AND k.table_name = tc.table_name
  WHERE tc.table_schema = 'public'
    AND tc.table_name = 'subdealer_discount_master_ref'
    AND tc.constraint_type = 'PRIMARY KEY';

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'subdealer_discount_master_ref'
  ) THEN
    RAISE NOTICE '22c: subdealer_discount_master_ref not found, skipping';
  ELSIF pk_col_count = 0 THEN
    RAISE NOTICE '22c: no primary key on subdealer_discount_master_ref, skipping';
  ELSIF pk_col_count >= 4 THEN
    RAISE NOTICE '22c: primary key already includes 4+ columns, skipping';
  ELSIF pk_col_count = 3 THEN
    RAISE NOTICE '22c: extending subdealer_discount_master_ref primary key to include model ...';
    -- Exact duplicate rows in key space (e.g. re-runs) — keep one
    DELETE FROM subdealer_discount_master_ref t
    USING subdealer_discount_master_ref t2
    WHERE t.ctid < t2.ctid
      AND t.dealer_id = t2.dealer_id
      AND t.subdealer_type = t2.subdealer_type
      AND t.valid_flag = t2.valid_flag
      AND BTRIM(t.model::text) = BTRIM(t2.model::text);

    ALTER TABLE subdealer_discount_master_ref DROP CONSTRAINT subdealer_discount_master_ref_pkey;
    ALTER TABLE subdealer_discount_master_ref
      ADD CONSTRAINT subdealer_discount_master_ref_pkey
        PRIMARY KEY (dealer_id, subdealer_type, valid_flag, model);

    COMMENT ON COLUMN subdealer_discount_master_ref.subdealer_type IS
      'Subdealer category / line key; with dealer_id, valid_flag, and model forms the primary key';
    COMMENT ON COLUMN subdealer_discount_master_ref.model IS
      'Vehicle model; part of the primary key; discount applies to this model string';
    RAISE NOTICE '22c: primary key now (dealer_id, subdealer_type, valid_flag, model)';
  ELSE
    RAISE NOTICE '22c: unexpected PK column count %, skipping; inspect manually', pk_col_count;
  END IF;
END
$m$;
