-- DMS Playwright: relation line, father/husband name, and whether CRM already has the contact.
-- Run against database: auto_ai (after customer_master exists).

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS dms_relation_prefix VARCHAR(8),
  ADD COLUMN IF NOT EXISTS father_or_husband_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS dms_contact_path VARCHAR(16) NOT NULL DEFAULT 'found';

COMMENT ON COLUMN customer_master.dms_relation_prefix IS 'DMS enquiry relation label: S/O or W/o (details sheet / operator)';
COMMENT ON COLUMN customer_master.father_or_husband_name IS 'Father or husband name for DMS S/O or W/o line';
COMMENT ON COLUMN customer_master.dms_contact_path IS 'Playwright branch: found = contact exists in DMS; new_enquiry = register enquiry then find again';
