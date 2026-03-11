-- Customer master data.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS customer_master (
    aadhar CHAR(12) NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    pin CHAR(6),
    city TEXT,
    state TEXT,
    phone VARCHAR(16),
    file_location TEXT
);

COMMENT ON TABLE customer_master IS 'Customer master; aadhar is 12-digit ID';
COMMENT ON COLUMN customer_master.file_location IS 'Location/sub-folder name where scans are placed';
