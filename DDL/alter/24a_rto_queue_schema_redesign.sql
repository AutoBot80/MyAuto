-- Redesign rto_queue: new serial PK, drop denormalized columns, add insurance_id FK.
-- Run after: 12c_rename_rto_payment_details_to_rto_queue.sql, 12e_rto_queue_batch_processing.sql.
-- Idempotent: uses IF EXISTS / IF NOT EXISTS throughout.

-- 1) Drop dependent views and FKs that reference old columns
DROP VIEW IF EXISTS form_vahan_view;

DO $$
BEGIN
  IF to_regclass('public.rc_status_sms_queue') IS NOT NULL THEN
    ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_rto;
    ALTER TABLE rc_status_sms_queue DROP CONSTRAINT IF EXISTS fk_rc_rto_sales_dealer;
  END IF;
END $$;
DROP INDEX IF EXISTS idx_rto_queue_sales_id_dealer_id;

-- 2) Drop old constraints
ALTER TABLE rto_queue DROP CONSTRAINT IF EXISTS uq_rto_customer_vehicle;
ALTER TABLE rto_queue DROP CONSTRAINT IF EXISTS rto_queue_pkey;
ALTER TABLE rto_queue DROP CONSTRAINT IF EXISTS rto_payment_details_pkey;

-- 3) Add new PK column
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS rto_queue_id SERIAL;
-- Ensure sales_id unique (may already exist)
ALTER TABLE rto_queue DROP CONSTRAINT IF EXISTS uq_rto_sales_id;
ALTER TABLE rto_queue ADD CONSTRAINT uq_rto_sales_id UNIQUE (sales_id);
-- Set new PK
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rto_queue_pkey' AND conrelid = 'rto_queue'::regclass) THEN
    ALTER TABLE rto_queue ADD CONSTRAINT rto_queue_pkey PRIMARY KEY (rto_queue_id);
  END IF;
END $$;

-- 4) Add new columns
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS insurance_id INTEGER;
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS customer_mobile VARCHAR(16);
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS rto_application_id VARCHAR(128);
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS rto_application_date DATE;
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS rto_payment_id VARCHAR(64);
ALTER TABLE rto_queue ADD COLUMN IF NOT EXISTS rto_payment_amount NUMERIC(12,2);

-- 5) Migrate data from old columns where possible
UPDATE rto_queue SET customer_mobile = mobile WHERE customer_mobile IS NULL AND mobile IS NOT NULL;
UPDATE rto_queue SET rto_application_id = vahan_application_id WHERE rto_application_id IS NULL AND vahan_application_id IS NOT NULL;
UPDATE rto_queue SET rto_application_date = register_date WHERE rto_application_date IS NULL AND register_date IS NOT NULL;
UPDATE rto_queue SET rto_payment_id = pay_txn_id WHERE rto_payment_id IS NULL AND pay_txn_id IS NOT NULL;
UPDATE rto_queue SET rto_payment_amount = rto_fees WHERE rto_payment_amount IS NULL AND rto_fees IS NOT NULL;

-- 6) Add FK for insurance_id
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_rto_insurance') THEN
    ALTER TABLE rto_queue ADD CONSTRAINT fk_rto_insurance FOREIGN KEY (insurance_id) REFERENCES insurance_master(insurance_id);
  END IF;
END $$;

-- 7) Drop old columns
ALTER TABLE rto_queue DROP COLUMN IF EXISTS application_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS customer_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS vehicle_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS dealer_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS name;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS mobile;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS chassis_num;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS vahan_application_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS register_date;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS rto_fees;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS pay_txn_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS operator_id;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS payment_date;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS rto_status;
ALTER TABLE rto_queue DROP COLUMN IF EXISTS subfolder;

-- 8) Update indexes
DROP INDEX IF EXISTS idx_rto_queue_dealer_created_at;
CREATE INDEX IF NOT EXISTS idx_rto_queue_status ON rto_queue (status);

-- 9) Recreate form_vahan_view with new schema
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

-- 10) Fix rc_status_sms_queue FK (point through sales_master, not rto_queue).
-- Skip if table missing, or if legacy table has no sales_id yet (run alter/11a after backfilling sales_id).
DO $$
BEGIN
  IF to_regclass('public.rc_status_sms_queue') IS NULL THEN
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'rc_status_sms_queue'
      AND column_name = 'sales_id'
  ) THEN
    RAISE NOTICE 'rc_status_sms_queue has no sales_id column; skipping fk_rc_sales. Add/populate sales_id then run DDL/alter/11a_rc_status_sms_queue_dealer_fk_via_sales.sql (update FROM clause to rto_queue if needed).';
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_rc_sales' AND conrelid = 'rc_status_sms_queue'::regclass
  ) THEN
    ALTER TABLE rc_status_sms_queue ADD CONSTRAINT fk_rc_sales FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id);
  END IF;
END $$;
