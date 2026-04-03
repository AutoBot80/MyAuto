-- Hero MISP proposal: CPA add-on row label varies (NIC / CPI / etc.); automation uses dealer_ref.hero_cpi (Y/N).
-- Y = ensure checkbox checked; N = ensure unchecked if present.
-- Run after: dealer_ref has prefer_insurer (16a). Recreates form_insurance_view.

ALTER TABLE dealer_ref
    ADD COLUMN IF NOT EXISTS hero_cpi CHAR(1);

UPDATE dealer_ref
SET hero_cpi = 'N'
WHERE hero_cpi IS NULL;

ALTER TABLE dealer_ref
    ALTER COLUMN hero_cpi SET DEFAULT 'N';

ALTER TABLE dealer_ref
    ALTER COLUMN hero_cpi SET NOT NULL;

ALTER TABLE dealer_ref
    DROP CONSTRAINT IF EXISTS chk_dealer_ref_hero_cpi;

ALTER TABLE dealer_ref
    ADD CONSTRAINT chk_dealer_ref_hero_cpi CHECK (hero_cpi IN ('Y', 'N'));

COMMENT ON COLUMN dealer_ref.hero_cpi IS
    'Y or N: MISP proposal CPA bottom add-on (NIC/CPI/Hero CPI — label varies by insurer); Y check, N uncheck — see fill_hero_insurance_service';

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
