#!/usr/bin/env bash
# Apply all DDL scripts (base tables + alters) in dependency order.
# Safe to re-run: base tables use IF NOT EXISTS, alters use IF NOT EXISTS / DO $$ guards.
# Usage:  bash /tmp/DDL/apply_all.sh "$DATABASE_URL"

set -euo pipefail

DB_URL="${1:?Usage: bash apply_all.sh \$DATABASE_URL}"
DDL_DIR="$(cd "$(dirname "$0")" && pwd)"

run() {
  local f="$1"
  local path="$DDL_DIR/$f"
  if [ ! -f "$path" ]; then
    echo "SKIP (not found): $f"
    return 0
  fi
  echo "--- $f ---"
  psql "$DB_URL" -v ON_ERROR_STOP=1 -f "$path" || { echo "FAILED: $f"; exit 1; }
}

echo "=== BASE TABLES ==="
run 01_ai_reader_queue.sql
run 02_customer_master.sql
run 03_vehicle_master.sql
run 04a_oem_ref.sql
run 04b_dealer_ref.sql
run 04c_oem_service_schedule.sql
run 05_sales_master.sql
run 06_insurance_master.sql
run 08_service_reminders_queue.sql
run 09_trigger_sales_master_sync_service_reminders.sql
run 10_rto_queue.sql
run 11_rc_status_sms_queue.sql
run 12_bulk_loads.sql
run 18_vehicle_inventory_master.sql
run 23_challan_master_staging.sql
run 24_challan_details_staging.sql
run 20_challan_master.sql
run 21_challan_details.sql
run 22_subdealer_discount_master_ref.sql
run 25_roles_ref.sql
run 26_login_ref.sql
run 27_login_roles_ref.sql
run 28_master_ref.sql

echo ""
echo "=== ALTER SCRIPTS ==="

# customer_master alters
run alter/02a_customer_master_add_file_location.sql
run alter/02b_customer_master_customer_id_pk.sql
run alter/02c_customer_master_add_gender_dob.sql
run alter/02d_customer_master_add_mobile_number.sql
run alter/02e_customer_master_add_profession.sql
run alter/02f_customer_master_drop_phone_unique_aadhar_mobile.sql
run alter/02g_customer_master_add_finance_marital_nominee_gender.sql
run alter/02h_customer_master_add_alt_phone_num.sql
run alter/02i_customer_master_add_dms_automation_fields.sql
run alter/02j_customer_master_add_care_of.sql
run alter/02k_customer_master_add_dms_contact_id.sql
run alter/02l_customer_master_mobile_number_bigint.sql

# vehicle_master alters
run alter/03a_vehicle_master_add_model_colour.sql
run alter/03b_vehicle_master_add_raw_fields.sql
run alter/03c_vehicle_master_add_mfg_capacity_body.sql
run alter/03d_vehicle_master_unique_raw_triple.sql
run alter/03e_vehicle_master_form20_fields.sql
run alter/03f_vehicle_master_add_total_amount.sql
run alter/03g_vehicle_master_rename_total_amount_to_vehicle_price.sql
run alter/03h_vehicle_master_add_rto_scrape_fields.sql
run alter/03i_vehicle_master_unique_engine_chassis.sql
run alter/03j_vehicle_master_rename_vehicle_price_to_vehicle_ex_showroom_price.sql

# dealer / oem alters
run alter/04a_dealer_master_add_dealer_of.sql
run alter/04b_rename_dealer_master_to_dealer_ref_and_oem.sql
run alter/04c_dealer_ref_add_auto_sms_reminders.sql
run alter/04d_drop_oem_service_frequency_add_oem_service_schedule.sql
run alter/04f_oem_service_schedule_add_reminder_type.sql
run alter/04g_oem_ref_add_dms_link.sql
run alter/04h_dealer_ref_add_rto_name.sql

# sales_master alters
run alter/05a_sales_master_add_dealer_unique.sql
run alter/05b_sales_master_add_sales_id_pk.sql
run alter/05c_rto_payment_details_add_sales_id_fk.sql
run alter/05d_service_reminders_queue_add_sales_id_fk.sql
run alter/05e_sales_master_add_file_location.sql
run alter/05f_sales_master_add_rto_scrape_fields.sql
run alter/05g_drop_vehicle_master_rto_scrape_fields.sql
run alter/05h_sales_master_add_order_invoice_numbers.sql
run alter/05i_sales_master_add_enquiry_number.sql

# insurance_master alters
run alter/06a_insurance_master_insurance_year_unique.sql
run alter/06b_insurance_master_fk_to_sales_only.sql

# service_reminders alters
run alter/08a_service_reminders_queue_add_reminder_date.sql
run alter/08b_service_reminders_queue_add_reminder_type_dealer_id.sql
run alter/08c_service_reminders_queue_fk_to_sales_only.sql

# trigger update
run alter/09a_trigger_sales_master_use_sales_id.sql

# rto / forms / views alters
run alter/10a_rto_payment_details_new_schema.sql
run alter/10b_rto_payment_details_add_subfolder.sql
run alter/10c_rto_payment_details_upsert_unique.sql
run alter/10d_rto_payment_details_fk_dealer_via_sales.sql
run alter/10e_form_vahan_view.sql
run alter/10f_form_dms_view.sql
run alter/10g_form_dms_view_extend_automation.sql
run alter/10h_form_dms_view_city_vehicle_dms_sku.sql
run alter/10i_form_dms_view_add_battery.sql
run alter/10j_form_insurance_view.sql

# rc_status_sms_queue alters
run alter/11a_rc_status_sms_queue_dealer_fk_via_sales.sql
run alter/11b_rc_status_sms_queue_dealer_fk_via_rto_queue.sql

# bulk_loads alters
run alter/12a_bulk_loads_add_dealer_id.sql
run alter/12b_bulk_loads_queue_redesign.sql
run alter/12c_rename_rto_payment_details_to_rto_queue.sql
run alter/12d_drop_bulk_loads_archive.sql
run alter/12e_rto_queue_batch_processing.sql
run alter/12f_rto_queue_add_staging_id.sql
run alter/12g_rto_queue_add_dealer_id.sql
run alter/12i_insurance_master_drop_insurance_automation_completed.sql

# add_sales_staging + form view drops
run alter/13a_add_sales_staging.sql
run alter/13b_drop_form_dms_view.sql
run alter/13c_add_sales_staging_login_id.sql
run alter/13d_add_sales_staging_subfolder.sql

# insurance_master nominee/cost
run alter/14a_nominee_gender_insurance_drop_customer_legacy.sql
run alter/14b_insurance_master_add_insurance_cost.sql
run alter/14c_insurance_master_drop_insurance_cost.sql

# vehicle_master variant/vin
run alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql
run alter/15b_vehicle_master_drop_horse_power.sql

# dealer_ref prefer_insurer, hero_cpi + form_insurance_view
run alter/16a_dealer_ref_prefer_insurer_form_insurance_view.sql
run alter/17a_dealer_ref_hero_cpi_form_insurance_view.sql

# challan / inventory alters
run alter/18a_challan_master_add_order_invoice_totals.sql
run alter/18b_vehicle_inventory_master_add_discount.sql
run alter/19a_challan_staging_batch_status.sql
run alter/19b_challan_staging_created_at.sql

# subdealer rename
run alter/22a_rename_subdealer_discount_master_to_ref.sql

# challan_master_staging last_run_at
run alter/23a_challan_master_staging_last_run_at.sql

# rto_queue schema redesign (must run after all earlier rto alters)
run alter/24a_rto_queue_schema_redesign.sql

# login_ref redesign
run alter/26b_login_ref_redesign.sql

# drop legacy ai_reader_queue (last — no dependents)
run alter/01z_drop_ai_reader_queue.sql
run alter/01a_ai_reader_queue_add_classification.sql

echo ""
echo "=== ALL DONE ==="
