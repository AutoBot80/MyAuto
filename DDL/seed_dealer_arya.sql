-- Insert Hero Motors (parent) and Arya Agencies into dealer_master.
-- Run once: psql -h localhost -U postgres -d auto_ai -f DDL/seed_dealer_arya.sql

INSERT INTO dealer_master (
    dealer_id,
    dealer_name,
    dealer_of,
    address,
    pin,
    city,
    state,
    parent_id,
    phone
) VALUES (
    100000,
    'Hero Motors',
    'hero Motors',
    'New Delhi',
    '110001',
    'New Delhi',
    'New Delhi',
    100000,
    '1111111111'
)
ON CONFLICT (dealer_id) DO NOTHING;

INSERT INTO dealer_master (
    dealer_id,
    dealer_name,
    dealer_of,
    address,
    pin,
    city,
    state,
    parent_id,
    phone
) VALUES (
    100001,
    'Arya Agencies',
    'Hero Motors',
    'Bharatpur, Rajasthan',
    '321001',
    'Bharatpur',
    'Rajasthan',
    100000,
    '9413112499'
)
ON CONFLICT (dealer_id) DO NOTHING;
