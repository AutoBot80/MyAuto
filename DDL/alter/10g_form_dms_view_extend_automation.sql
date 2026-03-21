-- Extend form_dms_view for full dummy DMS Playwright flow (enquiry, finance, relation).
-- Run after: 10f_form_dms_view.sql (base view).
--
-- The following ADD COLUMN blocks are idempotent: if you never ran 02g / 02h / 02i, this script
-- still succeeds (adds financier, landline, DMS fields, then recreates the view).

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS financier VARCHAR(255),
  ADD COLUMN IF NOT EXISTS marital_status VARCHAR(32),
  ADD COLUMN IF NOT EXISTS nominee_gender VARCHAR(16);

COMMENT ON COLUMN customer_master.financier IS 'Financier name captured from details sheet / insurance context';
COMMENT ON COLUMN customer_master.marital_status IS 'Customer marital status captured from details sheet';
COMMENT ON COLUMN customer_master.nominee_gender IS 'Nominee gender captured from details sheet';

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS alt_phone_num VARCHAR(16);

COMMENT ON COLUMN customer_master.alt_phone_num IS 'Alternate/landline customer number from Sales Detail Sheet (Alternate)';

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS dms_relation_prefix VARCHAR(8),
  ADD COLUMN IF NOT EXISTS father_or_husband_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS dms_contact_path VARCHAR(16) NOT NULL DEFAULT 'found';

COMMENT ON COLUMN customer_master.dms_relation_prefix IS 'DMS enquiry relation label: S/O or W/o (details sheet / operator)';
COMMENT ON COLUMN customer_master.father_or_husband_name IS 'Father or husband name for DMS S/O or W/o line';
COMMENT ON COLUMN customer_master.dms_contact_path IS 'Playwright branch: found = contact exists in DMS; new_enquiry = register enquiry then find again';

DROP VIEW IF EXISTS form_dms_view;

CREATE OR REPLACE VIEW form_dms_view AS
SELECT
    sm.sales_id,
    sm.customer_id,
    sm.vehicle_id,
    sm.dealer_id,
    COALESCE(sm.file_location, cm.file_location) AS subfolder,
    dr.dealer_name,
    vm.oem_name,
    CASE
        WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'Ms.'
        ELSE 'Mr.'
    END AS "Mr/Ms",
    SPLIT_PART(TRIM(COALESCE(cm.name, '')), ' ', 1) AS "Contact First Name",
    NULLIF(BTRIM(SUBSTRING(TRIM(COALESCE(cm.name, '')) FROM LENGTH(SPLIT_PART(TRIM(COALESCE(cm.name, '')), ' ', 1)) + 1)), '') AS "Contact Last Name",
    cm.mobile_number::text AS "Mobile Phone #",
    cm.alt_phone_num AS "Landline #",
    UPPER(COALESCE(cm.state, '')) AS "State",
    cm.address AS "Address Line 1",
    cm.pin AS "Pin Code",
    LEFT(COALESCE(vm.raw_key_num, vm.key_num, ''), 8) AS "Key num (partial)",
    LEFT(COALESCE(vm.raw_frame_num, vm.chassis, ''), 12) AS "Frame / Chassis num (partial)",
    LEFT(COALESCE(vm.raw_engine_num, vm.engine, ''), 12) AS "Engine num (partial)",
    COALESCE(NULLIF(BTRIM(cm.dms_relation_prefix), ''), CASE WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'W/o' ELSE 'S/O' END) AS "Relation (S/O or W/o)",
    cm.father_or_husband_name AS "Father or Husband Name",
    COALESCE(BTRIM(cm.financier), '') AS "Financier Name",
    CASE WHEN COALESCE(BTRIM(cm.financier), '') <> '' THEN 'Y' ELSE 'N' END AS "Finance Required",
    COALESCE(NULLIF(BTRIM(cm.dms_contact_path), ''), 'found') AS "DMS Contact Path"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id;
