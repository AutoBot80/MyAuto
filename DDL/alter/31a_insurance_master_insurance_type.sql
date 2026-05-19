-- insurance_master: distinguish Hero GI (Main) vs Alliance CPA (CPA) per sale/year.
-- Replaces uq_insurance_customer_vehicle_year with (customer_id, vehicle_id, insurance_year, insurance_type).
-- Run against database: auto_ai
-- Prerequisite: insurance_master exists (06 + 06a).

ALTER TABLE insurance_master
    ADD COLUMN IF NOT EXISTS insurance_type VARCHAR(16) NOT NULL DEFAULT 'Main';

COMMENT ON COLUMN insurance_master.insurance_type IS
    'Policy channel: Main = Hero/MISP GI; CPA = Alliance CPA certificate (separate row per sale/year)';

-- Existing rows (if any were created before DEFAULT applied)
UPDATE insurance_master
SET insurance_type = 'Main'
WHERE insurance_type IS NULL OR TRIM(insurance_type) = '';

ALTER TABLE insurance_master
    DROP CONSTRAINT IF EXISTS uq_insurance_customer_vehicle_year;

ALTER TABLE insurance_master
    DROP CONSTRAINT IF EXISTS uq_insurance_customer_vehicle_year_type;

ALTER TABLE insurance_master
    ADD CONSTRAINT uq_insurance_customer_vehicle_year_type
    UNIQUE (customer_id, vehicle_id, insurance_year, insurance_type);

ALTER TABLE insurance_master
    DROP CONSTRAINT IF EXISTS chk_insurance_master_type;

ALTER TABLE insurance_master
    ADD CONSTRAINT chk_insurance_master_type
    CHECK (insurance_type IN ('Main', 'CPA'));

-- Hero MISP automation: latest GI row only
DROP VIEW IF EXISTS form_insurance_view;

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
    WHERE insurance_type = 'Main'
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
    COALESCE(vm.engine, vm.raw_engine_num, '') AS full_engine,
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

COMMENT ON VIEW form_insurance_view IS
    'Per sale: customer/vehicle/dealer; latest insurance_master row where insurance_type=Main';

-- CPA portal fill: latest CPA row nominee fields
DROP VIEW IF EXISTS form_cpa_insurance_view;

CREATE OR REPLACE VIEW form_cpa_insurance_view AS
WITH latest_insurance AS (
    SELECT DISTINCT ON (customer_id, vehicle_id)
        customer_id,
        vehicle_id,
        insurance_id,
        insurer,
        policy_num,
        premium,
        nominee_name,
        nominee_age,
        nominee_relationship,
        nominee_gender,
        policy_to,
        insurance_year
    FROM insurance_master
    WHERE insurance_type = 'CPA'
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
    sm.dealer_id,
    COALESCE(cm.name, '') AS customer_name,
    COALESCE(cm.gender, '') AS gender,
    COALESCE(TRIM(cm.date_of_birth), '') AS date_of_birth,
    COALESCE(cm.mobile_number::text, '') AS mobile_number,
    COALESCE(cm.state, '') AS state,
    COALESCE(cm.city, '') AS city,
    COALESCE(cm.pin::text, '') AS pin_code,
    COALESCE(cm.address, '') AS address,
    COALESCE(vm.chassis, vm.raw_frame_num, '') AS frame_no,
    COALESCE(vm.chassis, vm.raw_frame_num, '') AS full_chassis,
    COALESCE(vm.engine, vm.raw_engine_num, '') AS engine_no,
    COALESCE(vm.engine, vm.raw_engine_num, '') AS full_engine,
    COALESCE(vm.model, '') AS model,
    COALESCE(vm.year_of_mfg::text, '') AS year_of_mfg,
    COALESCE(li.nominee_name, '') AS nominee_name,
    COALESCE(li.nominee_age::text, '') AS nominee_age,
    COALESCE(li.nominee_relationship, '') AS nominee_relationship,
    COALESCE(li.nominee_gender, '') AS nominee_gender,
    COALESCE(li.policy_num, '') AS cpa_policy_num
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN latest_insurance li
    ON li.customer_id = sm.customer_id
   AND li.vehicle_id = sm.vehicle_id;

COMMENT ON VIEW form_cpa_insurance_view IS
    'Per sale for CPA Alliance fill: customer/vehicle; latest insurance_master where insurance_type=CPA';
