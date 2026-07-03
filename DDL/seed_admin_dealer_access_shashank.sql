-- One-time setup: dealer 100003 + Admin Saathi scope for shashank (100001 + 100003).
-- Run against database: auto_ai
-- Prerequisite: run DDL/alter/35a_admin_dealer_access_ref.sql first.

-- 1) Create dealer 100003 if missing (sub-dealer under Hero Motors parent 100000).
INSERT INTO dealer_ref (
    dealer_id,
    dealer_name,
    oem_id,
    address,
    pin,
    city,
    state,
    rto_name,
    parent_id,
    phone,
    prefer_insurer,
    cpi_reqd
) VALUES (
    100003,
    'Test Dealer 100003',
    (SELECT oem_id FROM oem_ref WHERE oem_name = 'Hero Motors' LIMIT 1),
    'Test locality',
    '321001',
    'Bharatpur',
    'Rajasthan',
    'RTO-Bharatpur',
    100000,
    '9999999999',
    'Universal Sompo General Insurance',
    'Y'
)
ON CONFLICT (dealer_id) DO NOTHING;

-- 2) Map shashank to both dealers for Admin Saathi (Usage + Dealers tab).
INSERT INTO admin_dealer_access_ref (login_id, dealer_id)
VALUES ('shashank', 100001)
ON CONFLICT DO NOTHING;

INSERT INTO admin_dealer_access_ref (login_id, dealer_id)
VALUES ('shashank', 100003)
ON CONFLICT DO NOTHING;

-- Verify:
-- SELECT ada.login_id, ada.dealer_id, dr.dealer_name
-- FROM admin_dealer_access_ref ada
-- JOIN dealer_ref dr ON dr.dealer_id = ada.dealer_id
-- WHERE ada.login_id = 'shashank'
-- ORDER BY ada.dealer_id;
