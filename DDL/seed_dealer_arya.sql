-- Insert OEM and dealers (Hero Motors parent, Arya Agencies). Uses dealer_ref and oem_ref.
-- Run once after oem_ref and dealer_ref exist: psql -h localhost -U postgres -d auto_ai -f DDL/seed_dealer_arya.sql

INSERT INTO oem_ref (oem_name, vehicles_type)
SELECT 'Hero Motors', '2W'
WHERE NOT EXISTS (SELECT 1 FROM oem_ref WHERE oem_name = 'Hero Motors');

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
    phone
) VALUES (
    100000,
    'Hero Motors',
    (SELECT oem_id FROM oem_ref WHERE oem_name = 'Hero Motors' LIMIT 1),
    'New Delhi',
    '110001',
    'New Delhi',
    'New Delhi',
    100000,
    '1111111111'
)
ON CONFLICT (dealer_id) DO NOTHING;

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
    prefer_insurer
) VALUES (
    100001,
    'Arya Agencies',
    (SELECT oem_id FROM oem_ref WHERE oem_name = 'Hero Motors' LIMIT 1),
    'Bharatpur, Rajasthan',
    '321001',
    'Bharatpur',
    'Rajasthan',
    'RTO-Bharatpur',
    100000,
    '9413112499',
    'Universal Sompo General Insurance'
)
ON CONFLICT (dealer_id) DO NOTHING;
