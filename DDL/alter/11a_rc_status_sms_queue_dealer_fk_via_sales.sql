-- rc_status_sms_queue: add sales_id, change dealer_id FK from dealer_ref to sales_master.
-- dealer_id validated via sales_master (sales_id, dealer_id).
-- Run after: 05b, 05c (sales_id in sales_master).

-- 1) Add sales_id column
ALTER TABLE rc_status_sms_queue ADD COLUMN IF NOT EXISTS sales_id INTEGER;

-- 2) Populate from sales_master (matches on customer_id, vehicle_id)
UPDATE rc_status_sms_queue rc
SET sales_id = sm.sales_id
FROM sales_master sm
WHERE rc.customer_id = sm.customer_id AND rc.vehicle_id = sm.vehicle_id;

-- 3) Enforce NOT NULL (fail if any row has no matching rto_payment_details)
ALTER TABLE rc_status_sms_queue ALTER COLUMN sales_id SET NOT NULL;

-- 4) Drop dealer FK to dealer_ref
ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_dealer;

-- 5) Add FKs to sales_master (sales_id; sales_id+dealer_id for dealer validation)
ALTER TABLE rc_status_sms_queue ADD CONSTRAINT fk_rc_sales
  FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id);
ALTER TABLE rc_status_sms_queue ADD CONSTRAINT fk_rc_sales_dealer
  FOREIGN KEY (sales_id, dealer_id) REFERENCES sales_master(sales_id, dealer_id);

COMMENT ON COLUMN rc_status_sms_queue.sales_id IS 'FK to sales_master; dealer_id validated via sales_master';
