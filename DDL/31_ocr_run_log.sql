-- Add Sales OCR runs where at least one expected field could not be derived (append-only admin log).

CREATE TABLE IF NOT EXISTS ocr_run_log (
    id                BIGSERIAL PRIMARY KEY,
    dealer_id         INTEGER NOT NULL REFERENCES dealer_ref (dealer_id),
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    customer_mobile   TEXT NULL,
    sale_subfolder    TEXT NOT NULL,
    ocr_failures      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ocr_run_log_occurred_at_desc
    ON ocr_run_log (occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_ocr_run_log_customer_mobile
    ON ocr_run_log (customer_mobile)
    WHERE customer_mobile IS NOT NULL;
