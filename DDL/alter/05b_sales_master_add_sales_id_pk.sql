-- Add sales_id as auto-generated PK to sales_master.
-- Replaces composite PK (customer_id, vehicle_id) with sales_id; keeps (customer_id, vehicle_id) UNIQUE.
-- Run after: 05_sales_master (and optionally 05a).
-- Child tables (rto_payment_details, service_reminders_queue) are updated in 05c and 05d.
-- insurance_master keeps FK to (customer_id, vehicle_id) which remains UNIQUE.

-- 1) Add sales_id column (SERIAL = auto-increment)
ALTER TABLE sales_master ADD COLUMN IF NOT EXISTS sales_id SERIAL;

-- 2) Drop FKs that reference sales_master (must drop before changing PK)
ALTER TABLE insurance_master DROP CONSTRAINT IF EXISTS fk_insurance_sales;

ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS fk_rto_sales;
ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS fk_rto_dealer;

ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_sales;
ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_customer;
ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_vehicle;
ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_dealer;

-- 3) Drop old PK
ALTER TABLE sales_master DROP CONSTRAINT IF EXISTS sales_master_pkey;

-- 4) Make sales_id the new PK
ALTER TABLE sales_master ADD PRIMARY KEY (sales_id);

-- 5) Ensure (customer_id, vehicle_id) remains unique (one sale per customer/vehicle)
ALTER TABLE sales_master DROP CONSTRAINT IF EXISTS uq_sales_customer_vehicle_dealer;
ALTER TABLE sales_master ADD CONSTRAINT uq_sales_customer_vehicle UNIQUE (customer_id, vehicle_id);

-- 6) Restore insurance_master FK (references UNIQUE customer_id, vehicle_id)
ALTER TABLE insurance_master ADD CONSTRAINT fk_insurance_sales
  FOREIGN KEY (customer_id, vehicle_id) REFERENCES sales_master(customer_id, vehicle_id);

COMMENT ON COLUMN sales_master.sales_id IS 'Auto-generated PK; used by rto_payment_details and service_reminders_queue';
