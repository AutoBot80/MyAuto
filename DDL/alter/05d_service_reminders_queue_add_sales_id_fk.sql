-- service_reminders_queue: add sales_id, FK to sales_master(sales_id). Remove composite FK.
-- Run after: 05b_sales_master_add_sales_id_pk.

-- 1) Add sales_id column
ALTER TABLE service_reminders_queue ADD COLUMN IF NOT EXISTS sales_id INTEGER;

-- 2) Populate from sales_master
UPDATE service_reminders_queue srq
SET sales_id = sm.sales_id
FROM sales_master sm
WHERE srq.customer_id = sm.customer_id AND srq.vehicle_id = sm.vehicle_id;

-- 3) Enforce NOT NULL (fail if any row has no matching sale)
ALTER TABLE service_reminders_queue ALTER COLUMN sales_id SET NOT NULL;

-- 4) Add FK
ALTER TABLE service_reminders_queue ADD CONSTRAINT fk_service_reminders_sales
  FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id);

COMMENT ON COLUMN service_reminders_queue.sales_id IS 'FK to sales_master; replaces composite FK (customer_id, vehicle_id, dealer_id)';
