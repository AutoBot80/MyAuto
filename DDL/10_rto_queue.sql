-- RTO work queue: one row per sale. Populated after Fill Forms / Print Forms.
-- rto_queue_id is PK (auto); sales_id FK to sales_master (UNIQUE).
-- Run after: 02_customer_master, 03_vehicle_master, 05_sales_master, 06_insurance_master.

CREATE TABLE IF NOT EXISTS rto_queue (
  rto_queue_id          SERIAL PRIMARY KEY,
  sales_id              INTEGER NOT NULL,
  insurance_id          INTEGER,
  customer_mobile       VARCHAR(16),
  rto_application_id    VARCHAR(128),
  rto_application_date  DATE,
  rto_payment_id        VARCHAR(64),
  rto_payment_amount    NUMERIC(12,2),
  status                VARCHAR(32) NOT NULL DEFAULT 'Queued',
  processing_session_id VARCHAR(128),
  worker_id             VARCHAR(128),
  leased_until          TIMESTAMPTZ,
  attempt_count         INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  started_at            TIMESTAMPTZ,
  uploaded_at           TIMESTAMPTZ,
  finished_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_rto_sales_id UNIQUE (sales_id),
  CONSTRAINT fk_rto_sales FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id),
  CONSTRAINT fk_rto_insurance FOREIGN KEY (insurance_id) REFERENCES insurance_master(insurance_id)
);

CREATE INDEX IF NOT EXISTS idx_rto_queue_status ON rto_queue (status);
CREATE INDEX IF NOT EXISTS idx_rto_queue_session_status ON rto_queue (processing_session_id, status);
CREATE INDEX IF NOT EXISTS idx_rto_queue_lease ON rto_queue (leased_until);

COMMENT ON TABLE rto_queue IS 'RTO work queue; rto_queue_id PK (serial); sales_id FK+UNIQUE to sales_master';
