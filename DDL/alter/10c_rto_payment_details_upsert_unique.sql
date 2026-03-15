-- Add UNIQUE on (customer_id, vehicle_id) for upsert by Playwright.
-- One RTO payment row per sale; new registration updates existing row.
-- First remove duplicates, keeping the newest row per (customer_id, vehicle_id).

DELETE FROM rto_payment_details a
USING rto_payment_details b
WHERE a.customer_id = b.customer_id AND a.vehicle_id = b.vehicle_id
  AND a.created_at < b.created_at;

ALTER TABLE rto_payment_details DROP CONSTRAINT IF EXISTS rto_payment_details_customer_vehicle_unique;
ALTER TABLE rto_payment_details ADD CONSTRAINT rto_payment_details_customer_vehicle_unique
  UNIQUE (customer_id, vehicle_id);
