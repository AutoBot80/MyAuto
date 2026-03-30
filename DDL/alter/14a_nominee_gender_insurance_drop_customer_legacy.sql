-- Move nominee_gender to insurance_master; drop customer_master legacy columns;
-- refresh form_dms_view / form_insurance_view (Relation + Father/Husband from care_of only).
-- Run against database: auto_ai

DROP VIEW IF EXISTS form_dms_view;
DROP VIEW IF EXISTS form_insurance_view;

ALTER TABLE insurance_master
    ADD COLUMN IF NOT EXISTS nominee_gender VARCHAR(16);

COMMENT ON COLUMN insurance_master.nominee_gender IS 'Nominee gender from staging until policy commit; details sheet / OCR';

UPDATE insurance_master im
SET nominee_gender = cm.nominee_gender
FROM customer_master cm
WHERE im.customer_id = cm.customer_id
  AND cm.nominee_gender IS NOT NULL
  AND BTRIM(COALESCE(cm.nominee_gender, '')) <> ''
  AND (im.nominee_gender IS NULL OR BTRIM(COALESCE(im.nominee_gender, '')) = '');

ALTER TABLE customer_master DROP COLUMN IF EXISTS father_or_husband_name;
ALTER TABLE customer_master DROP COLUMN IF EXISTS nominee_gender;

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
    COALESCE(vm.battery, '') AS "Battery No",
    LEFT(COALESCE(vm.raw_frame_num, vm.chassis, ''), 12) AS "Frame / Chassis num (partial)",
    LEFT(COALESCE(vm.raw_engine_num, vm.engine, ''), 12) AS "Engine num (partial)",
    COALESCE(
        NULLIF(BTRIM(cm.dms_relation_prefix), ''),
        CASE
            WHEN LENGTH(BTRIM(COALESCE(cm.address, ''))) >= 3 THEN LEFT(BTRIM(cm.address), 3)
            ELSE CASE
                WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'D/o'
                ELSE 'S/o'
            END
        END
    ) AS "Relation (S/O or W/o)",
    BTRIM(COALESCE(cm.care_of, '')) AS "Father or Husband Name",
    COALESCE(BTRIM(cm.financier), '') AS "Financier Name",
    CASE WHEN COALESCE(BTRIM(cm.financier), '') <> '' THEN 'Y' ELSE 'N' END AS "Finance Required",
    COALESCE(NULLIF(BTRIM(cm.dms_contact_path), ''), 'found') AS "DMS Contact Path"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id;

CREATE OR REPLACE VIEW form_insurance_view AS
WITH latest_insurance AS (
    SELECT DISTINCT ON (customer_id, vehicle_id)
        customer_id,
        vehicle_id,
        insurance_id,
        insurer,
        nominee_name,
        nominee_age,
        nominee_relationship,
        nominee_gender,
        policy_to,
        insurance_year
    FROM insurance_master
    ORDER BY
        customer_id,
        vehicle_id,
        policy_to DESC NULLS LAST,
        insurance_year DESC NULLS LAST,
        insurance_id DESC
)
SELECT
    sm.sales_id,
    sm.customer_id,
    sm.vehicle_id,
    COALESCE(cm.name, '') AS customer_name,
    COALESCE(cm.gender, '') AS gender,
    COALESCE(TRIM(cm.date_of_birth), '') AS dob,
    COALESCE(cm.marital_status, '') AS marital_status,
    COALESCE(cm.profession, '') AS profession,
    COALESCE(cm.mobile_number::text, '') AS mobile_number,
    COALESCE(cm.alt_phone_num, '') AS alt_phone_num,
    COALESCE(cm.state, '') AS state,
    COALESCE(cm.city, '') AS city,
    COALESCE(cm.pin::text, '') AS pin_code,
    COALESCE(cm.address, '') AS address,
    COALESCE(vm.chassis, vm.raw_frame_num, '') AS frame_no,
    COALESCE(vm.chassis, vm.raw_frame_num, '') AS full_chassis,
    COALESCE(vm.engine, vm.raw_engine_num, '') AS engine_no,
    COALESCE(vm.model, '') AS model_name,
    COALESCE(vm.fuel_type, '') AS fuel_type,
    COALESCE(vm.year_of_mfg::text, '') AS year_of_mfg,
    COALESCE(vm.vehicle_ex_showroom_price::text, '') AS vehicle_price,
    COALESCE(NULLIF(TRIM(vm.oem_name), ''), oem_dealer.oem_name, '') AS oem_name,
    COALESCE(li.nominee_gender, '') AS nominee_gender,
    COALESCE(cm.financier, '') AS financer_name,
    COALESCE(dr.rto_name, '') AS rto_name,
    COALESCE(li.insurer, '') AS insurer,
    COALESCE(li.nominee_name, '') AS nominee_name,
    COALESCE(li.nominee_age::text, '') AS nominee_age,
    COALESCE(li.nominee_relationship, '') AS nominee_relationship
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
LEFT JOIN oem_ref oem_dealer ON oem_dealer.oem_id = dr.oem_id
LEFT JOIN latest_insurance li
    ON li.customer_id = sm.customer_id
   AND li.vehicle_id = sm.vehicle_id;

COMMENT ON VIEW form_insurance_view IS 'Single-row projection per sale: customer_master, vehicle_master, latest insurance_master row (by policy_to/year/id), dealer_ref; nominee_gender from insurance_master';
