-- Subdealer model discount rules. Run after dealer_ref.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS subdealer_discount_master (
    subdealer_discount_id SERIAL PRIMARY KEY,
    dealer_id INTEGER NOT NULL,
    model VARCHAR(64) NOT NULL,
    discount NUMERIC(12, 2),
    create_date VARCHAR(20),
    valid_flag CHAR(1) NOT NULL DEFAULT 'Y',
    CONSTRAINT fk_subdealer_discount_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT chk_subdealer_discount_valid_flag CHECK (valid_flag IN ('Y', 'N'))
);

COMMENT ON TABLE subdealer_discount_master IS 'Per-dealer model discount configuration';
COMMENT ON COLUMN subdealer_discount_master.create_date IS 'dd/mm/yyyy';
COMMENT ON COLUMN subdealer_discount_master.valid_flag IS 'Y = active, N = inactive';
