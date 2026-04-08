-- Consolidated challan header. Run after dealer_ref.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS challan_master (
    challan_id SERIAL PRIMARY KEY,
    challan_date VARCHAR(20),
    challan_book_num VARCHAR(64),
    dealer_from INTEGER NOT NULL,
    dealer_to INTEGER NOT NULL,
    num_vehicles INTEGER,
    order_number VARCHAR(128),
    invoice_number VARCHAR(128),
    total_ex_showroom_price NUMERIC(12, 2),
    total_discount NUMERIC(12, 2),
    CONSTRAINT fk_challan_master_dealer_from FOREIGN KEY (dealer_from) REFERENCES dealer_ref(dealer_id),
    CONSTRAINT fk_challan_master_dealer_to FOREIGN KEY (dealer_to) REFERENCES dealer_ref(dealer_id)
);

COMMENT ON TABLE challan_master IS 'Challan header: book/date, from/to dealers, vehicle count';
COMMENT ON COLUMN challan_master.challan_date IS 'dd/mm/yyyy';
COMMENT ON COLUMN challan_master.order_number IS 'DMS or book order reference';
COMMENT ON COLUMN challan_master.invoice_number IS 'Invoice reference when applicable';
COMMENT ON COLUMN challan_master.total_ex_showroom_price IS 'Sum of ex-showroom across lines';
COMMENT ON COLUMN challan_master.total_discount IS 'Total discount for the challan';
