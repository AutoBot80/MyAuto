-- Customer master data.
-- customer_id is PK; (aadhar last 4 + mobile_number) uniquely identify a customer.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS customer_master (
    customer_id SERIAL PRIMARY KEY,
    aadhar CHAR(4) NOT NULL,
    name TEXT NOT NULL,
    address TEXT,
    pin CHAR(6),
    city TEXT,
    state TEXT,
    mobile_number INTEGER,
    alt_phone_num VARCHAR(16),
    profession VARCHAR(16),
    file_location TEXT,
    gender VARCHAR(8),
    date_of_birth VARCHAR(20),
    CONSTRAINT uq_customer_aadhar_mobile UNIQUE (aadhar, mobile_number)
);

COMMENT ON TABLE customer_master IS 'Customer master; customer_id is PK; aadhar stores last 4 digits only';
COMMENT ON COLUMN customer_master.customer_id IS 'Auto-generated customer ID (integer)';
COMMENT ON COLUMN customer_master.aadhar IS 'Last 4 digits of Aadhar only';
COMMENT ON COLUMN customer_master.file_location IS 'Location/sub-folder name where scans are placed';
COMMENT ON COLUMN customer_master.date_of_birth IS 'Date of birth, dd/mm/yyyy (default date format for application and database)';
