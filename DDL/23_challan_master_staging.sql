-- Challan header staging (one row per Create Challans batch). Run after dealer_ref.
-- Replaces duplicating header fields on every line; pairs with challan_details_staging.

CREATE TABLE IF NOT EXISTS challan_master_staging (
    challan_batch_id UUID PRIMARY KEY,
    from_dealer_id INTEGER NOT NULL,
    to_dealer_id INTEGER NOT NULL,
    challan_date VARCHAR(20),
    challan_book_num VARCHAR(64),
    num_vehicles INTEGER NOT NULL,
    num_vehicles_prepared INTEGER NOT NULL DEFAULT 0,
    invoice_complete BOOLEAN NOT NULL DEFAULT FALSE,
    invoice_status VARCHAR(32) NOT NULL DEFAULT 'Pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_run_at TIMESTAMPTZ,
    CONSTRAINT fk_challan_master_staging_from FOREIGN KEY (from_dealer_id) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT fk_challan_master_staging_to FOREIGN KEY (to_dealer_id) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT chk_challan_master_staging_invoice_status CHECK (
        invoice_status IN ('Pending', 'Failed', 'Completed')
    )
);

CREATE INDEX IF NOT EXISTS idx_challan_master_staging_from_created
    ON challan_master_staging (from_dealer_id, created_at DESC);

COMMENT ON TABLE challan_master_staging IS 'Subdealer challan batch header before commit to challan_master';
COMMENT ON COLUMN challan_master_staging.invoice_status IS 'Pending | Failed | Completed';
COMMENT ON COLUMN challan_master_staging.num_vehicles IS 'Line count when Create Challan was pressed';
COMMENT ON COLUMN challan_master_staging.num_vehicles_prepared IS 'Lines that passed prepare_vehicle + inventory (Ready or Committed)';
COMMENT ON COLUMN challan_master_staging.last_run_at IS 'Set when process/retry DMS batch finishes';
