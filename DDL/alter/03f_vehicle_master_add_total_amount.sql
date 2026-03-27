-- Add vehicle_price to vehicle_master so downstream automation can read Vahan vehicle_price from DB views.
-- Run against database: auto_ai

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS vehicle_price NUMERIC(12, 2);

COMMENT ON COLUMN vehicle_master.vehicle_price IS 'Latest DMS vehicle price used as Vahan vehicle_price source; run DDL/alter/03j_vehicle_master_rename_vehicle_price_to_vehicle_ex_showroom_price.sql to rename to vehicle_ex_showroom_price';
