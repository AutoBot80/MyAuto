-- service_reminders_queue: replace FKs to customer_master/vehicle_master/dealer_ref
-- with single composite FK to sales_master (customer_id, vehicle_id, dealer_id).
-- Run after: 05a_sales_master_add_dealer_unique.
-- Prerequisite: Sync dealer_id from sales_master (run the UPDATE below if needed).

UPDATE service_reminders_queue srq
SET dealer_id = sm.dealer_id
FROM sales_master sm
WHERE srq.customer_id = sm.customer_id AND srq.vehicle_id = sm.vehicle_id
  AND (srq.dealer_id IS DISTINCT FROM sm.dealer_id);

ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_customer;
ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_vehicle;
ALTER TABLE service_reminders_queue DROP CONSTRAINT IF EXISTS fk_service_reminders_dealer;

ALTER TABLE service_reminders_queue
  ADD CONSTRAINT fk_service_reminders_sales FOREIGN KEY (customer_id, vehicle_id, dealer_id)
  REFERENCES sales_master(customer_id, vehicle_id, dealer_id);
