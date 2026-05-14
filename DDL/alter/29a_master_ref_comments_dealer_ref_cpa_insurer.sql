-- CPA third-party portals: master_ref.comments holds login URL when ref_type = 'CPA'.
-- dealer_ref.cpa_insurer references master_ref (ref_type CPA) via composite FK helper column.
-- Safe to re-run.

ALTER TABLE master_ref
    ADD COLUMN IF NOT EXISTS comments TEXT;

COMMENT ON COLUMN master_ref.comments IS
    'Optional notes. For ref_type CPA, stores the portal login URL (https://…). Other ref_types: optional notes or NULL.';

INSERT INTO master_ref (ref_type, ref_value, comments)
VALUES (
    'CPA',
    'Alliance Assure',
    'https://app.allianceassure.in/account/login?returnUrl=%2F'
)
ON CONFLICT (ref_type, ref_value) DO UPDATE
SET comments = EXCLUDED.comments;

ALTER TABLE dealer_ref
    ADD COLUMN IF NOT EXISTS cpa_insurer VARCHAR(512);

ALTER TABLE dealer_ref
    ADD COLUMN IF NOT EXISTS cpa_insurer_ref_type VARCHAR(64)
    GENERATED ALWAYS AS (
        CASE WHEN cpa_insurer IS NULL THEN NULL ELSE 'CPA'::varchar(64) END
    ) STORED;

ALTER TABLE dealer_ref
    DROP CONSTRAINT IF EXISTS fk_dealer_ref_cpa_insurer;

ALTER TABLE dealer_ref
    ADD CONSTRAINT fk_dealer_ref_cpa_insurer
    FOREIGN KEY (cpa_insurer_ref_type, cpa_insurer)
    REFERENCES master_ref (ref_type, ref_value);

COMMENT ON COLUMN dealer_ref.cpa_insurer IS
    'Optional CPA portal insurer (master_ref ref_type CPA); URL in master_ref.comments — DDL/alter/29a_master_ref_comments_dealer_ref_cpa_insurer.sql';

UPDATE dealer_ref
SET cpa_insurer = 'Alliance Assure'
WHERE cpa_insurer IS NULL;
