-- TNI dealers 100005–100009: default ND Cover, Rim Safeguard (not generic ND Cover fallback).
-- Run after: DDL/seed_insurance_addon_ref.sql (TNI preset rows), 37b on prod.
-- Run against database: auto_ai

BEGIN;

INSERT INTO insurance_addon_ref (insurer, display_label, nd_cover, rti, rim_safeguard, rsa, sort_order)
VALUES
  ('The New India Assurance Co. Ltd.', 'ND Cover, Rim Safeguard', 'Y', 'N', 'Y', 'N', 10),
  ('The New India Assurance Co. Ltd.', 'ND Cover',                'Y', 'N', 'N', 'N', 20)
ON CONFLICT (insurer, display_label) DO UPDATE SET
  nd_cover = EXCLUDED.nd_cover,
  rti = EXCLUDED.rti,
  rim_safeguard = EXCLUDED.rim_safeguard,
  rsa = EXCLUDED.rsa,
  sort_order = EXCLUDED.sort_order,
  active_flag = 'Y';

UPDATE dealer_ref d
SET insurance_addon = r.insurance_addon_id
FROM insurance_addon_ref r
WHERE d.dealer_id IN (100005, 100006, 100007, 100008, 100009)
  AND d.prefer_insurer = r.insurer
  AND r.display_label = 'ND Cover, Rim Safeguard';

-- Align existing staging snapshots for those dealers (optional explicit FK on in-process rows).
UPDATE add_sales_staging s
SET insurance_addon = d.insurance_addon
FROM dealer_ref d
WHERE s.dealer_id = d.dealer_id
  AND d.dealer_id IN (100005, 100006, 100007, 100008, 100009)
  AND d.insurance_addon IS NOT NULL
  AND s.insurance_addon IS DISTINCT FROM d.insurance_addon;

COMMIT;
