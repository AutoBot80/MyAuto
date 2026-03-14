-- Trigger on sales_master: on INSERT or UPDATE, refresh service_reminders_queue for that customer/vehicle.
-- Deletes existing reminders for the row's customer_id/vehicle_id. Inserts new reminder rows only when
-- dealer_ref.auto_sms_reminders = 'Y' (oem_id and oem_service_schedule used as before).
-- Run after sales_master, service_reminders_queue, dealer_ref, oem_service_schedule exist.
-- Run against database: auto_ai

CREATE OR REPLACE FUNCTION fn_sales_master_sync_service_reminders()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  v_oem_id INTEGER;
  v_auto_sms_reminders CHAR(1);
  sched RECORD;
BEGIN
  -- 1) Delete existing reminders for this customer_id and vehicle_id
  DELETE FROM service_reminders_queue
  WHERE customer_id = NEW.customer_id AND vehicle_id = NEW.vehicle_id;

  -- 2) Get oem_id and auto_sms_reminders from dealer_ref; inserts only if auto_sms_reminders = 'Y'
  IF NEW.dealer_id IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT oem_id, auto_sms_reminders INTO v_oem_id, v_auto_sms_reminders
  FROM dealer_ref
  WHERE dealer_id = NEW.dealer_id;

  IF v_oem_id IS NULL OR COALESCE(v_auto_sms_reminders, ' ') <> 'Y' THEN
    RETURN NEW;
  END IF;

  -- 3) Insert one row only: schedule with service_num = 1, reminder_num 1; reminder_type and dealer_id from schedule/sales
  INSERT INTO service_reminders_queue (
    customer_id,
    vehicle_id,
    billing_date,
    service_date,
    service_type,
    reminder_num,
    reminder_date,
    reminder_type,
    reminder_status,
    dealer_id
  )
  SELECT
    NEW.customer_id,
    NEW.vehicle_id,
    (NEW.billing_date)::date,
    (NEW.billing_date)::date + s.days_from_billing,
    s.service_type,
    1,
    (NEW.billing_date)::date + s.days_from_billing - 15,
    s.reminder_type,
    'Pending',
    NEW.dealer_id
  FROM oem_service_schedule s
  WHERE s.oem_id = v_oem_id AND s.active_flag = 'Y' AND s.service_num = 1
  LIMIT 1;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_sales_master_sync_service_reminders
  AFTER INSERT OR UPDATE ON sales_master
  FOR EACH ROW
  EXECUTE FUNCTION fn_sales_master_sync_service_reminders();

-- To replace the trigger (e.g. after changing the function), run first:
--   DROP TRIGGER IF EXISTS trg_sales_master_sync_service_reminders ON sales_master;

COMMENT ON FUNCTION fn_sales_master_sync_service_reminders() IS 'Refreshes service_reminders_queue when dealer_ref.auto_sms_reminders=Y; from dealer_ref + oem_service_schedule on sales_master upsert';
