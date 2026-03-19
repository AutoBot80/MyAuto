-- View of DMS-facing values from sales/customer/vehicle/dealer data.
-- Run after: sales_master, customer_master, vehicle_master, dealer_ref.

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
    UPPER(COALESCE(cm.state, '')) AS "State",
    cm.address AS "Address Line 1",
    cm.pin AS "Pin Code",
    LEFT(COALESCE(vm.raw_key_num, vm.key_num, ''), 8) AS "Key num (partial)",
    LEFT(COALESCE(vm.raw_frame_num, vm.chassis, ''), 12) AS "Frame / Chassis num (partial)",
    LEFT(COALESCE(vm.raw_engine_num, vm.engine, ''), 12) AS "Engine num (partial)"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id;
