-- Read-only view: one row per sale with customer, vehicle, dealer, latest insurance — for Hero/MISP automation.
-- Uses only existing columns on customer_master, vehicle_master, insurance_master (plus dealer_ref, oem_ref, sales_master).
-- Run after: sales_master, customer_master, vehicle_master, dealer_ref, oem_ref, insurance_master.

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
    COALESCE(cm.nominee_gender, '') AS nominee_gender,
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

COMMENT ON VIEW form_insurance_view IS 'Single-row projection per sale: customer_master, vehicle_master, latest insurance_master row (by policy_to/year/id), dealer_ref; for chassis/nominee/KYC fields — proposal UI defaults (email, add-ons, payment) remain hardcoded in Playwright';
