-- Terminal process failures (client-visible errors) for admin diagnostics.
-- Upsert key: (dealer_id, process_label, entity_dedupe_key)

CREATE TABLE IF NOT EXISTS process_failure_log (
    id                  BIGSERIAL PRIMARY KEY,
    dealer_id           INTEGER NOT NULL REFERENCES dealer_ref (dealer_id),
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    process_label       TEXT NOT NULL,
    customer_mobile     TEXT NULL,
    challan_book_num    TEXT NULL,
    challan_date        TEXT NULL,
    challan_batch_id    UUID NULL,
    rto_queue_id        INTEGER NULL,
    error_text          TEXT NOT NULL,
    entity_dedupe_key   TEXT NOT NULL,
    CONSTRAINT uq_process_failure_log_dedupe UNIQUE (dealer_id, process_label, entity_dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_process_failure_log_occurred_at_desc
    ON process_failure_log (occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_process_failure_log_customer_mobile
    ON process_failure_log (customer_mobile)
    WHERE customer_mobile IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_process_failure_log_challan_book_num
    ON process_failure_log (challan_book_num)
    WHERE challan_book_num IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_process_failure_log_rto_queue_id
    ON process_failure_log (rto_queue_id)
    WHERE rto_queue_id IS NOT NULL;
