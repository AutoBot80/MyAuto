-- Challan line items: vehicles on a challan. Run after challan_master and vehicle_inventory_master.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS challan_details (
    challan_id INTEGER NOT NULL,
    inventory_line_id INTEGER NOT NULL,
    CONSTRAINT pk_challan_details PRIMARY KEY (challan_id, inventory_line_id),
    CONSTRAINT fk_challan_details_challan FOREIGN KEY (challan_id) REFERENCES challan_master(challan_id) ON DELETE CASCADE,
    CONSTRAINT fk_challan_details_inventory FOREIGN KEY (inventory_line_id) REFERENCES vehicle_inventory_master(inventory_line_id)
);

COMMENT ON TABLE challan_details IS 'Links challans to vehicle inventory lines';
