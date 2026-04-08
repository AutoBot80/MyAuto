-- Vehicle inventory lines (yard/stock). Run after dealer_ref.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS vehicle_inventory_master (
    inventory_line_id SERIAL PRIMARY KEY,
    from_company_date VARCHAR(20),
    sold_date VARCHAR(20),
    dealer_id INTEGER NOT NULL,
    yard_id VARCHAR(64),
    chassis_no VARCHAR(64),
    engine_no VARCHAR(64),
    battery VARCHAR(64),
    "key" VARCHAR(64),
    model VARCHAR(64),
    variant VARCHAR(64),
    color VARCHAR(64),
    cubic_capacity NUMERIC(10, 2),
    vehicle_type VARCHAR(32),
    ex_showroom_price NUMERIC(12, 2),
    discount NUMERIC(12, 2),
    CONSTRAINT fk_vehicle_inventory_master_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref(dealer_id)
);

COMMENT ON TABLE vehicle_inventory_master IS 'Stock/inventory lines per dealer (chassis, engine, pricing)';
COMMENT ON COLUMN vehicle_inventory_master.from_company_date IS 'dd/mm/yyyy';
COMMENT ON COLUMN vehicle_inventory_master.sold_date IS 'dd/mm/yyyy';
COMMENT ON COLUMN vehicle_inventory_master."key" IS 'Physical key identifier';
COMMENT ON COLUMN vehicle_inventory_master.discount IS 'Discount amount for this inventory line';
