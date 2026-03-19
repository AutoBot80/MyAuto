-- View of VAHAN-facing values from sales/customer/vehicle/dealer/insurance/RTO data.
-- Run after: sales_master, dealer_ref, insurance_master, and rto_queue schema changes.

DROP VIEW IF EXISTS form_vahan_view;

CREATE OR REPLACE VIEW form_vahan_view AS
WITH latest_insurance AS (
    SELECT DISTINCT ON (customer_id, vehicle_id)
        customer_id,
        vehicle_id,
        insurance_year,
        idv,
        insurer,
        policy_num,
        policy_from,
        policy_to,
        nominee_name,
        nominee_age,
        nominee_relationship,
        policy_broker,
        premium
    FROM insurance_master
    ORDER BY customer_id, vehicle_id, insurance_year DESC, insurance_id DESC
),
latest_rto AS (
    SELECT DISTINCT ON (customer_id, vehicle_id)
        application_id,
        vahan_application_id,
        sales_id,
        customer_id,
        vehicle_id,
        dealer_id,
        name,
        mobile,
        chassis_num,
        register_date,
        rto_fees,
        status,
        pay_txn_id,
        operator_id,
        payment_date,
        rto_status,
        subfolder,
        created_at
    FROM rto_queue
    ORDER BY customer_id, vehicle_id, created_at DESC, application_id DESC
)
SELECT
    sm.sales_id,
    sm.customer_id,
    sm.vehicle_id,
    COALESCE(lr.dealer_id, sm.dealer_id) AS dealer_id,
    COALESCE(lr.subfolder, sm.file_location, cm.file_location) AS subfolder,
    lr.application_id AS queue_id,
    lr.vahan_application_id AS application_id,
    lr.pay_txn_id,
    lr.status AS rto_payment_status,
    lr.rto_fees,
    ('RTO' || COALESCE(lr.dealer_id, sm.dealer_id)::text) AS rto_dealer_id,
    vm.model AS vehicle_model,
    vm.colour AS vehicle_colour,
    COALESCE(vm.fuel_type, 'Petrol') AS fuel_type,
    COALESCE(vm.year_of_mfg::text, TO_CHAR(sm.billing_date::date, 'YYYY')) AS year_of_mfg,
    vm.vehicle_price AS vehicle_price,
    'New Registration'::text AS "Registration Type *",
    COALESCE(lr.chassis_num, vm.chassis, vm.raw_frame_num) AS "Chassis No *",
    RIGHT(COALESCE(vm.engine, vm.raw_engine_num, ''), 5) AS "Engine/Motor No (Last 5 Chars)",
    TO_CHAR(sm.billing_date::date, 'DD-MON-YYYY') AS "Purchase Delivery Date",
    'SELECT'::text AS "Do You want to Opt Choice Number / Fancy Number / Retention Number",
    COALESCE(lr.name, cm.name) AS "Owner Name *",
    'Individual'::text AS "Owner Type",
    NULL::text AS "Son/Wife/Daughter of",
    '1'::text AS "Ownership Serial",
    'Aadhaar OTP'::text AS "Aadhaar Mode",
    'General'::text AS "Category *",
    COALESCE(lr.mobile, cm.mobile_number::text) AS "Mobile No",
    NULL::text AS "PAN Card",
    NULL::text AS "Voter ID",
    CASE
        WHEN cm.aadhar IS NOT NULL THEN 'Last 4 in DB: ' || cm.aadhar
        ELSE NULL
    END AS "Aadhaar No",
    cm.address AS "Permanent Address",
    cm.address AS "House No & Street Name",
    cm.city AS "Village/Town/City",
    CASE
        WHEN li.policy_num IS NOT NULL OR li.insurer IS NOT NULL THEN 'Comprehensive'
        ELSE NULL
    END AS "Insurance Type",
    li.insurer AS "Insurer",
    li.policy_num AS "Policy No",
    CASE WHEN li.policy_from IS NOT NULL THEN TO_CHAR(li.policy_from, 'DD-MON-YYYY') END AS "Insurance From (DD-MMM-YYYY)",
    CASE WHEN li.policy_to IS NOT NULL THEN TO_CHAR(li.policy_to, 'DD-MON-YYYY') END AS "Insurance Upto (DD-MMM-YYYY)",
    COALESCE(li.idv::text, li.premium::text) AS "Insured Declared Value",
    'State Series'::text AS "Please Select Series Type",
    NULL::text AS "Financier / Bank",
    lr.vahan_application_id AS "Application No",
    ('Assigned Office & Action - ' || ('RTO' || COALESCE(lr.dealer_id, sm.dealer_id)::text)) AS "Assigned Office & Action",
    vm.plate_num AS "Registration No",
    CASE WHEN lr.rto_fees IS NOT NULL THEN lr.rto_fees::text END AS "Amount"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
LEFT JOIN latest_insurance li
    ON li.customer_id = sm.customer_id
   AND li.vehicle_id = sm.vehicle_id
LEFT JOIN latest_rto lr
    ON lr.customer_id = sm.customer_id
   AND lr.vehicle_id = sm.vehicle_id;
