-- Revert app DB state for one sale after a mistaken DMS Create Invoice commit.
-- Input: set p_sales_id in the DO block below. Run as a user with DELETE/UPDATE rights.
--
-- Does:
--   - Deletes child rows keyed by sales_id (RTO / reminders / SMS / payment details).
--   - Deletes insurance_master rows for that sale's (customer_id, vehicle_id).
--   - Deletes the sales_master row.
--   - Deletes customer_master / vehicle_master only when nothing else references them
--     (no other sales_master or insurance_master rows for that customer_id / vehicle_id).
-- Optional:
--   - Clears vehicle_inventory_master.sold_date when chassis/engine match vehicle_master (Siebel path).
--
-- Does NOT: modify add_sales_staging (left as-is). Does not cancel the invoice in Siebel/DMS.

DO $$
DECLARE
  p_sales_id           INTEGER := 0;       -- <<<<<< SET THIS (required)
  p_clear_inv_sold     BOOLEAN := FALSE;   -- TRUE only if sold_date was set by this sale's Siebel commit
  v_customer_id        INTEGER;
  v_vehicle_id         INTEGER;
  v_dealer_id          INTEGER;
  v_invoice            TEXT;
  n_rc                 INTEGER := 0;
  n_srq                INTEGER := 0;
  n_rpd                INTEGER := 0;
  n_rq                 INTEGER := 0;
  n_ins                INTEGER := 0;
  n_inv                INTEGER := 0;
  n_cust               INTEGER := 0;
  n_veh                INTEGER := 0;
BEGIN
  IF p_sales_id IS NULL OR p_sales_id <= 0 THEN
    RAISE EXCEPTION 'Set p_sales_id to a positive integer before running.';
  END IF;

  SELECT sm.customer_id, sm.vehicle_id, sm.dealer_id, sm.invoice_number::text
    INTO v_customer_id, v_vehicle_id, v_dealer_id, v_invoice
  FROM sales_master sm
  WHERE sm.sales_id = p_sales_id;

  IF v_customer_id IS NULL THEN
    RAISE EXCEPTION 'sales_master.sales_id=% not found (nothing to revert).', p_sales_id;
  END IF;

  RAISE NOTICE 'Reverting sales_id=% customer_id=% vehicle_id=% dealer_id=% invoice=%',
    p_sales_id, v_customer_id, v_vehicle_id, v_dealer_id, v_invoice;

  -- Optional: break diagnostic links to RTO rows we are about to remove
  IF to_regclass('public.process_failure_log') IS NOT NULL THEN
    UPDATE process_failure_log pfl
    SET rto_queue_id = NULL
    WHERE pfl.rto_queue_id IN (
      SELECT rq.rto_queue_id FROM rto_queue rq WHERE rq.sales_id = p_sales_id
    );
  END IF;

  DELETE FROM rc_status_sms_queue WHERE sales_id = p_sales_id;
  GET DIAGNOSTICS n_rc = ROW_COUNT;

  DELETE FROM service_reminders_queue WHERE sales_id = p_sales_id;
  GET DIAGNOSTICS n_srq = ROW_COUNT;

  DELETE FROM rto_payment_details WHERE sales_id = p_sales_id;
  GET DIAGNOSTICS n_rpd = ROW_COUNT;

  DELETE FROM rto_queue WHERE sales_id = p_sales_id;
  GET DIAGNOSTICS n_rq = ROW_COUNT;

  DELETE FROM insurance_master
  WHERE customer_id = v_customer_id AND vehicle_id = v_vehicle_id;
  GET DIAGNOSTICS n_ins = ROW_COUNT;

  DELETE FROM sales_master WHERE sales_id = p_sales_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'sales_master delete failed unexpectedly for sales_id=%', p_sales_id;
  END IF;

  -- Before dropping vehicle_master, optional inventory revert (matches app trim semantics)
  IF p_clear_inv_sold AND to_regclass('public.vehicle_inventory_master') IS NOT NULL THEN
    UPDATE vehicle_inventory_master vim
    SET sold_date = NULL
    FROM vehicle_master vm
    WHERE vm.vehicle_id = v_vehicle_id
      AND TRIM(COALESCE(vim.chassis_no, '')) = TRIM(COALESCE(vm.chassis, ''))
      AND TRIM(COALESCE(vim.engine_no, '')) = TRIM(COALESCE(vm.engine, ''))
      AND TRIM(COALESCE(vm.chassis, '')) <> ''
      AND TRIM(COALESCE(vm.engine, '')) <> '';
    GET DIAGNOSTICS n_inv = ROW_COUNT;
  END IF;

  DELETE FROM customer_master cm
  WHERE cm.customer_id = v_customer_id
    AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.customer_id = cm.customer_id)
    AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.customer_id = cm.customer_id);
  GET DIAGNOSTICS n_cust = ROW_COUNT;

  DELETE FROM vehicle_master vm
  WHERE vm.vehicle_id = v_vehicle_id
    AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.vehicle_id = vm.vehicle_id)
    AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.vehicle_id = vm.vehicle_id);
  GET DIAGNOSTICS n_veh = ROW_COUNT;

  IF n_cust = 0 THEN
    RAISE NOTICE 'customer_master row % not deleted (still referenced by another sale or insurance row, or already gone).',
      v_customer_id;
  END IF;
  IF n_veh = 0 THEN
    RAISE NOTICE 'vehicle_master row % not deleted (still referenced by another sale or insurance row, or already gone).',
      v_vehicle_id;
  END IF;

  RAISE NOTICE 'Deleted rc_status_sms_queue=% service_reminders_queue=% rto_payment_details=% rto_queue=% insurance_master=%',
    n_rc, n_srq, n_rpd, n_rq, n_ins;
  RAISE NOTICE 'Deleted sales_master; customer_master rows=% vehicle_master rows=%; vehicle_inventory sold_date clears=%',
    n_cust, n_veh, n_inv;
END;
$$ LANGUAGE plpgsql;
