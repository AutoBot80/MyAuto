-- Insurance master: policy records linked to customer and vehicle.
-- Run after customer_master, vehicle_master.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS insurance_master (
    insurance_id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    insurance_year INTEGER,
    idv NUMERIC(12, 2),
    insurer VARCHAR(255),
    policy_num VARCHAR(24),
    policy_from DATE,
    policy_to DATE,
    nominee_name TEXT,
    nominee_age INTEGER,
    nominee_relationship VARCHAR(64),
    policy_broker VARCHAR(255),
    premium NUMERIC(12, 2),
    CONSTRAINT fk_insurance_customer FOREIGN KEY (customer_id) REFERENCES customer_master(customer_id),
    CONSTRAINT fk_insurance_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicle_master(vehicle_id),
    CONSTRAINT uq_insurance_customer_vehicle_year UNIQUE (customer_id, vehicle_id, insurance_year)
);

COMMENT ON TABLE insurance_master IS 'Insurance policy records; insurance_id auto-generated';
COMMENT ON COLUMN insurance_master.insurance_year IS 'Insurance year (yyyy); new row uses current year';
COMMENT ON COLUMN insurance_master.idv IS 'Insured declared value';
COMMENT ON COLUMN insurance_master.policy_from IS 'Policy start date (store as DATE; display dd/mm/yyyy)';
COMMENT ON COLUMN insurance_master.policy_to IS 'Policy end date (store as DATE; display dd/mm/yyyy)';
COMMENT ON COLUMN insurance_master.premium IS 'Premium amount';
