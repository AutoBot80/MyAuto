-- Sales master: links customer, vehicle, dealer. Run after customer_master, vehicle_master, dealer_master.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS sales_master (
    aadhar CHAR(12) NOT NULL,
    vehicle_id INTEGER NOT NULL,
    billing_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dealer_id INTEGER,
    PRIMARY KEY (aadhar, vehicle_id),
    CONSTRAINT fk_sales_customer FOREIGN KEY (aadhar) REFERENCES customer_master(aadhar),
    CONSTRAINT fk_sales_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicle_master(vehicle_id),
    CONSTRAINT fk_sales_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_master(dealer_id)
);

COMMENT ON TABLE sales_master IS 'Sales records; composite PK (aadhar, vehicle_id)';
