-- RTO_Payment_Details: new schema with application_id as PK, FKs to sales_master.
-- Run after: 02_customer_master, 03_vehicle_master, 04b_dealer_ref, 05_sales_master.
-- Migrates from old rto_payment_details (drops and recreates; existing rows are lost).

DROP TABLE IF EXISTS rto_payment_details;

CREATE TABLE rto_payment_details (
  application_id   VARCHAR(128) PRIMARY KEY,
  customer_id      INTEGER NOT NULL,
  vehicle_id       INTEGER NOT NULL,
  dealer_id        INTEGER,
  name             VARCHAR(255),
  mobile           VARCHAR(16),
  chassis_num      VARCHAR(64),
  register_date    DATE NOT NULL DEFAULT CURRENT_DATE,
  rto_fees         NUMERIC(12,2) NOT NULL,
  status           VARCHAR(32) NOT NULL DEFAULT 'Pending',
  pay_txn_id       VARCHAR(64),
  operator_id      VARCHAR(64),
  payment_date     DATE,
  rto_status       VARCHAR(32) NOT NULL DEFAULT 'Registered',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_rto_sales FOREIGN KEY (customer_id, vehicle_id) REFERENCES sales_master(customer_id, vehicle_id),
  CONSTRAINT fk_rto_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref(dealer_id)
);

COMMENT ON TABLE rto_payment_details IS 'RTO registration applications; application_id PK; FKs to sales_master';
