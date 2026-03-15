-- Add UNIQUE (customer_id, vehicle_id, dealer_id) to sales_master so rto_payment_details
-- and service_reminders_queue can FK dealer_id through sales_master.
-- Run after: 05_sales_master.

ALTER TABLE sales_master
  ADD CONSTRAINT uq_sales_customer_vehicle_dealer UNIQUE (customer_id, vehicle_id, dealer_id);

COMMENT ON CONSTRAINT uq_sales_customer_vehicle_dealer ON sales_master IS
  'Allows FK from rto_payment_details and service_reminders_queue (customer_id, vehicle_id, dealer_id)';
