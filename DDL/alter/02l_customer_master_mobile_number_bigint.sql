-- Widen customer_master.mobile_number INTEGER -> BIGINT.
-- Indian 10-digit mobiles (6–9xxxxxxxx) exceed PostgreSQL INTEGER max (2_147_483_647);
-- inserts failed with "integer out of range" until this migration.
-- Run after: views that reference cm.mobile_number (form_vahan_view from 24a, form_insurance_view from 17a).
-- form_dms_view is dropped by alter/13b on full apply; drop here if it still exists (partial installs).
-- Idempotent: ALTER TYPE BIGINT succeeds if column is already BIGINT.

DROP VIEW IF EXISTS form_dms_view;
DROP VIEW IF EXISTS form_insurance_view;
DROP VIEW IF EXISTS form_vahan_view;

ALTER TABLE customer_master
    ALTER COLUMN mobile_number TYPE BIGINT;

COMMENT ON COLUMN customer_master.mobile_number IS
    'Customer mobile (10 digits, stored as BIGINT; INTEGER too small for 6–9… range)';

-- Recreate form_vahan_view (same definition as alter/24a_rto_queue_schema_redesign.sql §9).
CREATE OR REPLACE VIEW form_vahan_view AS
SELECT
    rq.sales_id,
    sm.billing_date,
    sm.dealer_id,
    COALESCE(dr.rto_name, 'RTO' || sm.dealer_id::text) AS dealer_rto,
    sm.customer_id,
    cm.mobile_number AS mobile,
    cm.name,
    cm.care_of,
    cm.address,
    cm.city,
    cm.state,
    cm.pin,
    cm.financier,
    sm.vehicle_id,
    vm.vehicle_type,
    COALESCE(vm.chassis, vm.raw_frame_num) AS chassis,
    RIGHT(COALESCE(vm.engine, vm.raw_engine_num, ''), 5) AS engine_short,
    vm.vehicle_ex_showroom_price AS ex_showroom_price,
    rq.insurance_id,
    im.insurer,
    im.policy_num,
    im.policy_from AS policy_from_date,
    im.idv
FROM rto_queue rq
JOIN sales_master sm ON sm.sales_id = rq.sales_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN insurance_master im ON im.insurance_id = rq.insurance_id;

-- Recreate form_insurance_view (same definition as alter/17a_dealer_ref_hero_cpi_form_insurance_view.sql VIEW only).
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
    COALESCE(NULLIF(TRIM(dr.prefer_insurer), ''), '') AS prefer_insurer,
    COALESCE(dr.hero_cpi, 'N') AS hero_cpi,
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
    'Single-row projection per sale: customer_master, vehicle_master, latest insurance_master row (by policy_to/year/id), dealer_ref (incl. prefer_insurer, hero_cpi); nominee_gender from insurance_master';
