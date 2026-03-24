-- Combined migration (run once after 10g_form_dms_view_extend_automation.sql):
-- 1) vehicle_master.dms_sku — Siebel SKU from DMS scrape (Fill DMS → update_vehicle_master_from_dms)
-- 2) form_dms_view — add customer city for Add Enquiry / Opportunities address fill

ALTER TABLE vehicle_master
    ADD COLUMN IF NOT EXISTS dms_sku VARCHAR(128);

COMMENT ON COLUMN vehicle_master.dms_sku IS 'SKU from Siebel vehicle scrape (Vehicle Information / list); persisted by Fill DMS';

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
    cm.city AS "City",
    cm.pin AS "Pin Code",
    LEFT(COALESCE(vm.raw_key_num, vm.key_num, ''), 8) AS "Key num (partial)",
    LEFT(COALESCE(vm.raw_frame_num, vm.chassis, ''), 12) AS "Frame / Chassis num (partial)",
    LEFT(COALESCE(vm.raw_engine_num, vm.engine, ''), 12) AS "Engine num (partial)",
    COALESCE(NULLIF(BTRIM(cm.dms_relation_prefix), ''), CASE WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'W/o' ELSE 'S/O' END) AS "Relation (S/O or W/o)",
    COALESCE(NULLIF(BTRIM(cm.care_of), ''), cm.father_or_husband_name) AS "Father or Husband Name",
    COALESCE(BTRIM(cm.financier), '') AS "Financier Name",
    CASE WHEN COALESCE(BTRIM(cm.financier), '') <> '' THEN 'Y' ELSE 'N' END AS "Finance Required",
    COALESCE(NULLIF(BTRIM(cm.dms_contact_path), ''), 'found') AS "DMS Contact Path"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id;
