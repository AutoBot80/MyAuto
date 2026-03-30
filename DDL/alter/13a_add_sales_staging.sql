-- Add Sales: server-side staging for deferred master commits (OCR → validate → Create Invoice → DB wave).
-- See Documentation/low-level-design.md §2.2a (Add Sales staging) and Database DDL.md.

CREATE TABLE IF NOT EXISTS add_sales_staging (
    staging_id UUID PRIMARY KEY,
    dealer_id INTEGER NOT NULL,
    payload_json JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NULL,
    CONSTRAINT fk_add_sales_staging_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref (dealer_id),
    CONSTRAINT chk_add_sales_staging_status CHECK (status IN ('draft', 'committed', 'abandoned'))
);

CREATE INDEX IF NOT EXISTS idx_add_sales_staging_dealer_updated
    ON add_sales_staging (dealer_id, updated_at DESC);

COMMENT ON TABLE add_sales_staging IS 'Validated Add Sales snapshot before customer/vehicle/sales master commit; Create Invoice loads by staging_id and persists masters on success.';
COMMENT ON COLUMN add_sales_staging.staging_id IS 'Opaque handle returned by POST /add-sales/staging; client passes to Create Invoice.';
COMMENT ON COLUMN add_sales_staging.payload_json IS 'Merged customer, vehicle, insurance, file_location (same shape as submit-info body).';
COMMENT ON COLUMN add_sales_staging.status IS 'draft: editable; committed: masters written (row may be retained for audit); abandoned: superseded or TTL.';
COMMENT ON COLUMN add_sales_staging.expires_at IS 'Optional TTL for cleanup jobs; NULL means no automatic expiry.';
