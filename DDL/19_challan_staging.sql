-- OCR/import staging for subdealer challans. Run after dealer_ref.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS challan_staging (
    challan_staging_id SERIAL PRIMARY KEY,
    challan_date VARCHAR(20),
    challan_book_num VARCHAR(64),
    from_dealer_id INTEGER NOT NULL,
    to_dealer_id INTEGER NOT NULL,
    raw_chassis VARCHAR(128),
    raw_engine VARCHAR(128),
    status VARCHAR(64),
    CONSTRAINT fk_challan_staging_from_dealer FOREIGN KEY (from_dealer_id) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT fk_challan_staging_to_dealer FOREIGN KEY (to_dealer_id) REFERENCES dealer_ref(dealer_id)
);

COMMENT ON TABLE challan_staging IS 'Pre-validated challan rows from OCR/import';
COMMENT ON COLUMN challan_staging.challan_date IS 'dd/mm/yyyy';
