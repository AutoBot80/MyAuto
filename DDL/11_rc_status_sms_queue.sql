-- RC status SMS queue: populated when RTO payment is done.
-- sales_id FK to sales_master; dealer_id validated via sales_master (sales_id, dealer_id).
-- Run after: 05_sales_master, 10_rto_queue.

CREATE TABLE IF NOT EXISTS rc_status_sms_queue (
  id                SERIAL PRIMARY KEY,
  sales_id          INTEGER NOT NULL,
  dealer_id         INTEGER,
  vehicle_id        INTEGER NOT NULL,
  customer_id       INTEGER NOT NULL,
  customer_mobile   VARCHAR(16),
  message_type      VARCHAR(64) NOT NULL,
  sms_status        VARCHAR(32) NOT NULL DEFAULT 'Pending',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_rc_sales FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id)
);

COMMENT ON TABLE rc_status_sms_queue IS 'SMS queue for RC status notifications; populated when payment is done';
