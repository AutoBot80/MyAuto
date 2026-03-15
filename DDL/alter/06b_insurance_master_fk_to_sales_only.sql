-- insurance_master: replace FKs to customer_master/vehicle_master with FK to sales_master only.
-- Run after: 05a_sales_master_add_dealer_unique (or ensure sales_master exists).
-- Prerequisite: All insurance_master rows must have matching (customer_id, vehicle_id) in sales_master.

ALTER TABLE insurance_master DROP CONSTRAINT IF EXISTS fk_insurance_customer;
ALTER TABLE insurance_master DROP CONSTRAINT IF EXISTS fk_insurance_vehicle;

ALTER TABLE insurance_master
  ADD CONSTRAINT fk_insurance_sales FOREIGN KEY (customer_id, vehicle_id)
  REFERENCES sales_master(customer_id, vehicle_id);
