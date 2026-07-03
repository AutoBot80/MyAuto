-- Seed insurance_addon_ref presets. Idempotent on (insurer, display_label).
-- Run after: DDL/37a_insurance_addon_ref.sql
-- Run against database: auto_ai

BEGIN;

INSERT INTO insurance_addon_ref (insurer, display_label, nd_cover, rti, rim_safeguard, rsa, sort_order)
VALUES
  ('BAJAJ GENERAL INSURANCE LIMITED', 'ND Cover, Rim Safeguard, RSA', 'Y', 'N', 'Y', 'Y', 10),
  ('BAJAJ GENERAL INSURANCE LIMITED', 'ND Cover, Rim Safeguard',      'Y', 'N', 'Y', 'N', 20),
  ('BAJAJ GENERAL INSURANCE LIMITED', 'ND Cover',                     'Y', 'N', 'N', 'N', 30)
ON CONFLICT (insurer, display_label) DO UPDATE SET
  nd_cover = EXCLUDED.nd_cover,
  rti = EXCLUDED.rti,
  rim_safeguard = EXCLUDED.rim_safeguard,
  rsa = EXCLUDED.rsa,
  sort_order = EXCLUDED.sort_order,
  active_flag = 'Y';

-- NIC: use exact master_ref label (prod spelling may differ from seed_master_ref_insurers.sql).
INSERT INTO insurance_addon_ref (insurer, display_label, nd_cover, rti, rim_safeguard, rsa, sort_order)
SELECT m.ref_value, v.display_label, v.nd_cover, v.rti, v.rim_safeguard, v.rsa, v.sort_order
FROM master_ref m
CROSS JOIN (
  VALUES
    ('ND Plus Cover', 'Y', 'N', 'N', 'N', 10),
    ('None',          'N', 'N', 'N', 'N', 20)
) AS v(display_label, nd_cover, rti, rim_safeguard, rsa, sort_order)
WHERE m.ref_type = 'INSURER'
  AND m.ref_value ILIKE 'National Insurance%'
ON CONFLICT (insurer, display_label) DO UPDATE SET
  nd_cover = EXCLUDED.nd_cover,
  rti = EXCLUDED.rti,
  rim_safeguard = EXCLUDED.rim_safeguard,
  rsa = EXCLUDED.rsa,
  sort_order = EXCLUDED.sort_order,
  active_flag = 'Y';

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

INSERT INTO insurance_addon_ref (insurer, display_label, nd_cover, rti, rim_safeguard, rsa, sort_order)
SELECT
  m.ref_value,
  'ND Cover',
  'Y', 'N', 'N', 'N',
  10
FROM master_ref m
WHERE m.ref_type = 'INSURER'
  AND UPPER(TRIM(COALESCE(m.comments, ''))) = 'Y'
  AND m.ref_value <> 'BAJAJ GENERAL INSURANCE LIMITED'
  AND m.ref_value NOT ILIKE 'National Insurance%'
  AND m.ref_value <> 'The New India Assurance Co. Ltd.'
ON CONFLICT (insurer, display_label) DO NOTHING;

COMMIT;
