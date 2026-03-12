-- Migration: customer_id as PK; aadhar last 4 only; unique (aadhar, phone).
-- sales_master: switch from aadhar to customer_id FK.
-- Run against database: auto_ai (after 02_customer_master and 05_sales_master exist).

BEGIN;

-- 1) customer_master: add customer_id, then make it PK and change aadhar to last 4
ALTER TABLE customer_master ADD COLUMN IF NOT EXISTS customer_id SERIAL;

-- Backfill customer_id for existing rows (SERIAL only sets default for new rows)
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN SELECT ctid FROM customer_master WHERE customer_id IS NULL ORDER BY aadhar
  LOOP
    UPDATE customer_master SET customer_id = nextval('customer_master_customer_id_seq') WHERE ctid = r.ctid;
  END LOOP;
END $$;

ALTER TABLE customer_master ALTER COLUMN customer_id SET NOT NULL;

-- Reduce aadhar to last 4 digits only
ALTER TABLE customer_master ALTER COLUMN aadhar TYPE CHAR(4) USING (RIGHT(TRIM(aadhar), 4));

-- Drop old PK and add new PK + unique
ALTER TABLE customer_master DROP CONSTRAINT IF EXISTS customer_master_pkey;
ALTER TABLE customer_master ADD PRIMARY KEY (customer_id);
ALTER TABLE customer_master DROP CONSTRAINT IF EXISTS uq_customer_aadhar_phone;
ALTER TABLE customer_master ADD CONSTRAINT uq_customer_aadhar_phone UNIQUE (aadhar, phone);

COMMENT ON COLUMN customer_master.aadhar IS 'Last 4 digits of Aadhar only';

-- 2) sales_master: add customer_id, populate from customer_master (match on last 4 of old aadhar)
ALTER TABLE sales_master ADD COLUMN IF NOT EXISTS customer_id INTEGER;

UPDATE sales_master s
SET customer_id = (
  SELECT c.customer_id FROM customer_master c
  WHERE c.aadhar = RIGHT(TRIM(s.aadhar), 4)
  ORDER BY c.customer_id LIMIT 1
);

-- Drop old FK and aadhar column, add new PK and FK
ALTER TABLE sales_master DROP CONSTRAINT IF EXISTS fk_sales_customer;
ALTER TABLE sales_master DROP CONSTRAINT IF EXISTS sales_master_pkey;
ALTER TABLE sales_master DROP COLUMN IF EXISTS aadhar;
ALTER TABLE sales_master ALTER COLUMN customer_id SET NOT NULL;
ALTER TABLE sales_master ADD PRIMARY KEY (customer_id, vehicle_id);
ALTER TABLE sales_master ADD CONSTRAINT fk_sales_customer FOREIGN KEY (customer_id) REFERENCES customer_master(customer_id);

COMMIT;
