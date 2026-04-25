-- Subdealer model discount rules. Run after dealer_ref.
-- Run against database: auto_ai
--
-- Primary key: (dealer_id, subdealer_type, valid_flag, model). No surrogate id.

CREATE TABLE IF NOT EXISTS subdealer_discount_master_ref (
    dealer_id INTEGER NOT NULL,
    subdealer_type VARCHAR(64) NOT NULL,
    valid_flag CHAR(1) NOT NULL DEFAULT 'Y',
    model VARCHAR(64) NOT NULL,
    discount NUMERIC(12, 2),
    create_date VARCHAR(20),
    CONSTRAINT subdealer_discount_master_ref_pkey PRIMARY KEY (dealer_id, subdealer_type, valid_flag, model),
    CONSTRAINT fk_subdealer_discount_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT chk_subdealer_discount_valid_flag CHECK (valid_flag IN ('Y', 'N'))
);

COMMENT ON TABLE subdealer_discount_master_ref IS 'Per-dealer subdealer-type discount (vehicle model and amount on each row)';
COMMENT ON COLUMN subdealer_discount_master_ref.subdealer_type IS
  'Subdealer category / line key; with dealer_id, valid_flag, and model forms the primary key';
COMMENT ON COLUMN subdealer_discount_master_ref.model IS
  'Leading substring of the DMS model (full DMS value may be longer; app: starts_with(BTRIM(dms), BTRIM(model)), longest key wins)';
COMMENT ON COLUMN subdealer_discount_master_ref.create_date IS 'dd/mm/yyyy';
COMMENT ON COLUMN subdealer_discount_master_ref.valid_flag IS 'Y = active, N = inactive';
