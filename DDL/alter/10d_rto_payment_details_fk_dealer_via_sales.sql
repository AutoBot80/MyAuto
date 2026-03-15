-- rto_payment_details: replace FK to dealer_ref with composite FK to sales_master
-- so dealer_id is validated through sales_master.
-- Run after: 05a_sales_master_add_dealer_unique.
-- Prerequisite: Sync dealer_id from sales_master (run the UPDATE below if needed).

UPDATE rto_payment_details rpd
SET dealer_id = sm.dealer_id
FROM sales_master sm
WHERE rpd.customer_id = sm.customer_id AND rpd.vehicle_id = sm.vehicle_id
  AND (rpd.dealer_id IS DISTINCT FROM sm.dealer_id);

ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS fk_rto_dealer;
ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS fk_rto_sales;

ALTER TABLE rto_payment_details
  ADD CONSTRAINT fk_rto_sales FOREIGN KEY (customer_id, vehicle_id, dealer_id)
  REFERENCES sales_master(customer_id, vehicle_id, dealer_id);
