-- Dealer master data.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS dealer_master (
    dealer_id SERIAL PRIMARY KEY,
    dealer_name VARCHAR(255) NOT NULL,
    address TEXT,
    pin CHAR(6),
    city TEXT,
    state TEXT,
    parent_id INTEGER,
    phone VARCHAR(16)
);

COMMENT ON TABLE dealer_master IS 'Dealer master; parent_id for hierarchy';
