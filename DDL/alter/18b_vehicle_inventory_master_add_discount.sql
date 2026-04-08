-- vehicle_inventory_master: per-line discount. Run against existing DB with vehicle_inventory_master.
-- Run against database: auto_ai

ALTER TABLE vehicle_inventory_master ADD COLUMN IF NOT EXISTS discount NUMERIC(12, 2);

COMMENT ON COLUMN vehicle_inventory_master.discount IS 'Discount amount for this inventory line';
