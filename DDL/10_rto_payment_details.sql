-- RTO payment tracking: one row per registration application (from dummy Vahan / real RTO).
-- Populated when Fill Forms completes the RTO registration step; status moves to Paid when payment is done.

CREATE TABLE IF NOT EXISTS rto_payment_details (
  id                SERIAL PRIMARY KEY,
  customer_id       INTEGER NOT NULL REFERENCES customer_master(customer_id),
  name              VARCHAR(255),
  mobile            VARCHAR(16),
  chassis_num       VARCHAR(64),
  application_num   VARCHAR(128) NOT NULL,
  submission_date   DATE NOT NULL DEFAULT CURRENT_DATE,
  rto_payment_due   NUMERIC(12,2) NOT NULL,
  status            VARCHAR(32) NOT NULL DEFAULT 'Pending',
  pos_mgr_id        VARCHAR(64),
  txn_id            VARCHAR(64),
  payment_date      DATE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE rto_payment_details IS 'RTO registration applications; status Pending until payment, then Paid with payment_date and txn_id';
