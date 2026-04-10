-- View of RTO/Vahan-facing values from sales/customer/vehicle/insurance/rto_queue data.
-- Run after: rto_queue, sales_master, customer_master, vehicle_master, insurance_master, dealer_ref.

DROP VIEW IF EXISTS form_vahan_view;

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
