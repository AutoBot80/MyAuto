-- Dealer default MISP add-on preset (must match prefer_insurer on referenced row).
-- Run after: DDL/37a_insurance_addon_ref.sql, DDL/seed_insurance_addon_ref.sql
-- Run against database: auto_ai

BEGIN;

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS insurance_addon INTEGER;

ALTER TABLE dealer_ref
  DROP CONSTRAINT IF EXISTS fk_dealer_ref_insurance_addon;

ALTER TABLE dealer_ref
  ADD CONSTRAINT fk_dealer_ref_insurance_addon
  FOREIGN KEY (insurance_addon)
  REFERENCES insurance_addon_ref (insurance_addon_id);

COMMENT ON COLUMN dealer_ref.insurance_addon IS
  'FK to insurance_addon_ref default preset; row.insurer must equal dealer_ref.prefer_insurer';

UPDATE dealer_ref d
SET insurance_addon = r.insurance_addon_id
FROM insurance_addon_ref r
WHERE d.dealer_id = 100001
  AND d.prefer_insurer = r.insurer
  AND r.display_label = 'ND Cover, Rim Safeguard, RSA';

UPDATE dealer_ref d
SET insurance_addon = r.insurance_addon_id
FROM insurance_addon_ref r
WHERE d.dealer_id = 100003
  AND d.prefer_insurer = r.insurer
  AND r.display_label = 'ND Cover, Rim Safeguard';

UPDATE dealer_ref d
SET insurance_addon = r.insurance_addon_id
FROM insurance_addon_ref r
WHERE d.dealer_id IN (100005, 100006, 100007, 100008, 100009)
  AND d.prefer_insurer = r.insurer
  AND r.display_label = 'ND Cover, Rim Safeguard';

UPDATE dealer_ref d
SET insurance_addon = sub.insurance_addon_id
FROM (
  SELECT DISTINCT ON (r.insurer)
    r.insurer,
    r.insurance_addon_id
  FROM insurance_addon_ref r
  WHERE r.active_flag = 'Y'
  ORDER BY r.insurer, r.sort_order, r.insurance_addon_id
) sub
WHERE d.insurance_addon IS NULL
  AND d.prefer_insurer IS NOT NULL
  AND TRIM(d.prefer_insurer) <> ''
  AND sub.insurer = d.prefer_insurer;

COMMIT;
