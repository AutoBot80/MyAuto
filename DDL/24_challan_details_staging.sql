-- Per-vehicle staging lines for subdealer challan. Run after challan_master_staging and dealer_ref.

CREATE TABLE IF NOT EXISTS challan_details_staging (
    challan_detail_staging_id SERIAL PRIMARY KEY,
    challan_batch_id UUID NOT NULL REFERENCES challan_master_staging(challan_batch_id) ON DELETE CASCADE,
    raw_chassis VARCHAR(128),
    raw_engine VARCHAR(128),
    status VARCHAR(64),
    last_error TEXT,
    inventory_line_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_challan_details_staging_inventory FOREIGN KEY (inventory_line_id)
        REFERENCES vehicle_inventory_master(inventory_line_id)
);

CREATE INDEX IF NOT EXISTS idx_challan_details_staging_batch ON challan_details_staging(challan_batch_id);
CREATE INDEX IF NOT EXISTS idx_challan_details_staging_status ON challan_details_staging(status);

COMMENT ON TABLE challan_details_staging IS 'Per-line OCR/import staging for subdealer challan batch';
COMMENT ON COLUMN challan_details_staging.status IS 'Queued | Ready | Failed | Committed';
