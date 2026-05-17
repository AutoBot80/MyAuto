-- CPA Alliance certificate form: sale-linked projection (customer + vehicle + latest CPA insurance row).
-- Pattern mirrors form_insurance_view; consumed by build_cpa_fill_values + add_alliance_cpa_insurance.
-- Run after: sales_master, customer_master, vehicle_master, insurance_master.
-- On existing DBs that already have insurance_type, prefer 31a_insurance_master_insurance_type.sql (recreates this view with CPA filter).

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
