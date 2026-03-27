-- Siebel DMS: persist scraped Enquiry# on the sale row.
-- Run after DDL/alter/05h_sales_master_add_order_invoice_numbers.sql.

ALTER TABLE sales_master
    ADD COLUMN IF NOT EXISTS enquiry_number VARCHAR(128);

COMMENT ON COLUMN sales_master.enquiry_number IS 'DMS Enquiry# scraped from Contact_Enquiry tab (Fill DMS)';
