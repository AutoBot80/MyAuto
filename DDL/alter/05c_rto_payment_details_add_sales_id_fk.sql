-- rto_payment_details: add sales_id, FK to sales_master(sales_id). Remove composite FK.
-- Run after: 05b_sales_master_add_sales_id_pk.

-- 1) Add sales_id column
ALTER TABLE rto_payment_details ADD COLUMN IF NOT EXISTS sales_id INTEGER;

-- 2) Populate from sales_master
UPDATE rto_payment_details rpd
SET sales_id = sm.sales_id
FROM sales_master sm
WHERE rpd.customer_id = sm.customer_id AND rpd.vehicle_id = sm.vehicle_id;

-- 3) Enforce NOT NULL (fail if any row has no matching sale)
ALTER TABLE rto_payment_details ALTER COLUMN sales_id SET NOT NULL;

-- 4) Add FK and UNIQUE (one RTO payment per sale); keep (customer_id, vehicle_id) UNIQUE for rc_status_sms_queue FK
ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS rto_payment_details_customer_vehicle_unique;
ALTER TABLE rto_payment_details ADD CONSTRAINT fk_rto_sales FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id);
ALTER TABLE rto_payment_details ADD CONSTRAINT uq_rto_sales_id UNIQUE (sales_id);
ALTER TABLE rto_payment_details ADD CONSTRAINT uq_rto_customer_vehicle UNIQUE (customer_id, vehicle_id);

COMMENT ON COLUMN rto_payment_details.sales_id IS 'FK to sales_master; replaces composite FK (customer_id, vehicle_id)';
