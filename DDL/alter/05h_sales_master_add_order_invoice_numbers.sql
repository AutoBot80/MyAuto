-- Siebel DMS: persist scraped Order# and Invoice# on the sale row.
-- Run after DDL/05_sales_master.sql (and dealer_id / sales_id alters as applicable).

ALTER TABLE sales_master
    ADD COLUMN IF NOT EXISTS order_number VARCHAR(128),
    ADD COLUMN IF NOT EXISTS invoice_number VARCHAR(128);

COMMENT ON COLUMN sales_master.order_number IS 'DMS Order# from Vehicle Sales / order header scrape (Fill DMS)';
COMMENT ON COLUMN sales_master.invoice_number IS 'DMS Invoice# when present on order / invoice view (Fill DMS)';
