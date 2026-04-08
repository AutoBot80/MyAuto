-- challan_master: order/invoice refs and price totals. Run against existing DB with challan_master.
-- Run against database: auto_ai

ALTER TABLE challan_master ADD COLUMN IF NOT EXISTS order_number VARCHAR(128);
ALTER TABLE challan_master ADD COLUMN IF NOT EXISTS invoice_number VARCHAR(128);
ALTER TABLE challan_master ADD COLUMN IF NOT EXISTS total_ex_showroom_price NUMERIC(12, 2);
ALTER TABLE challan_master ADD COLUMN IF NOT EXISTS total_discount NUMERIC(12, 2);

COMMENT ON COLUMN challan_master.order_number IS 'DMS or book order reference';
COMMENT ON COLUMN challan_master.invoice_number IS 'Invoice reference when applicable';
COMMENT ON COLUMN challan_master.total_ex_showroom_price IS 'Sum of ex-showroom across lines';
COMMENT ON COLUMN challan_master.total_discount IS 'Total discount for the challan';
