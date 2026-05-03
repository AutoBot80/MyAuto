-- subdealer_discount_master_ref: add subdealer_type, replace SERIAL PK with
--   PRIMARY KEY (dealer_id, subdealer_type, valid_flag, model); drop subdealer_discount_id.
-- Run on databases that still have the legacy surrogate key. Skips (no-op) if
-- the table already has the new primary key.
-- Run after dealer_ref, after DDL/22 (legacy shape) or 22a rename, and after any data load.
-- Run against database: auto_ai

DO $migration$
DECLARE
  has_table bool;
  legacy bool;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'subdealer_discount_master_ref'
  ) INTO has_table;
  IF NOT has_table THEN
    RAISE NOTICE '22b: subdealer_discount_master_ref not found, skipping';
  ELSE
    SELECT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'subdealer_discount_master_ref'
        AND column_name = 'subdealer_discount_id'
    ) INTO legacy;
    IF NOT legacy THEN
      RAISE NOTICE '22b: subdealer_discount_master_ref does not have legacy subdealer_discount_id, skipping (use 22c for 3- to 4-column PK)';

    ELSE
      RAISE NOTICE '22b: migrating subdealer_discount_master_ref to composite PK ...';

      -- Same (dealer, model, valid) — keep the newest surrogate row
      DELETE FROM subdealer_discount_master_ref t
      USING subdealer_discount_master_ref t2
      WHERE t.subdealer_discount_id < t2.subdealer_discount_id
        AND t.dealer_id = t2.dealer_id
        AND COALESCE(BTRIM(t.model::text), '') = COALESCE(BTRIM(t2.model::text), '')
        AND t.valid_flag = t2.valid_flag;

      ALTER TABLE subdealer_discount_master_ref ADD COLUMN IF NOT EXISTS subdealer_type VARCHAR(64);

      UPDATE subdealer_discount_master_ref
      SET
        subdealer_type = CASE
          WHEN BTRIM(COALESCE(model::text, '')) = '' THEN 'UNSPECIFIED'
          ELSE BTRIM(model::text)
        END
      WHERE subdealer_type IS NULL
         OR BTRIM(subdealer_type) = '';

      -- Same (dealer, subdealer_type, valid) — one row
      DELETE FROM subdealer_discount_master_ref t
      USING subdealer_discount_master_ref t2
      WHERE t.subdealer_discount_id < t2.subdealer_discount_id
        AND t.dealer_id = t2.dealer_id
        AND COALESCE(BTRIM(t.subdealer_type::text), '') = COALESCE(BTRIM(t2.subdealer_type::text), '')
        AND t.valid_flag = t2.valid_flag;

      ALTER TABLE subdealer_discount_master_ref ALTER COLUMN subdealer_type SET NOT NULL;

      ALTER TABLE subdealer_discount_master_ref DROP CONSTRAINT subdealer_discount_master_ref_pkey;

      ALTER TABLE subdealer_discount_master_ref ALTER COLUMN subdealer_discount_id DROP DEFAULT;
      DROP SEQUENCE IF EXISTS subdealer_discount_master_ref_subdealer_discount_id_seq;
      DROP SEQUENCE IF EXISTS subdealer_discount_master_subdealer_discount_id_seq;
      ALTER TABLE subdealer_discount_master_ref DROP COLUMN subdealer_discount_id;

      ALTER TABLE subdealer_discount_master_ref
        ADD CONSTRAINT subdealer_discount_master_ref_pkey
          PRIMARY KEY (dealer_id, subdealer_type, valid_flag, model);

      COMMENT ON COLUMN subdealer_discount_master_ref.subdealer_type IS
        'Subdealer category / line key; with dealer_id, valid_flag, and model forms the primary key';
      COMMENT ON COLUMN subdealer_discount_master_ref.model IS
        'Vehicle model; part of the primary key; discount applies to this model string';

      RAISE NOTICE '22b: subdealer_discount_master_ref migration complete';
    END IF;
  END IF;
END
$migration$;
