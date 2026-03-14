-- Rename year to insurance_year; unique (customer_id, vehicle_id, insurance_year) for upsert.
-- Run against database: auto_ai

ALTER TABLE insurance_master RENAME COLUMN year TO insurance_year;
ALTER TABLE insurance_master
  ADD CONSTRAINT uq_insurance_customer_vehicle_year UNIQUE (customer_id, vehicle_id, insurance_year);

COMMENT ON COLUMN insurance_master.insurance_year IS 'Insurance year (yyyy); new row uses current year';
