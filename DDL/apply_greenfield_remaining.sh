#!/usr/bin/env bash
# Apply remaining base DDL to RDS after ref tables already exist
# (oem_ref, dealer_ref, roles_ref, login_ref, login_roles_ref, master_ref, subdealer_discount_master_ref).
#
# Usage (EC2):
#   export DATABASE_URL="$(cd /opt/saathi/backend && /opt/saathi/venv/bin/python3 -c \
#     "from pathlib import Path; from dotenv import dotenv_values; print(dotenv_values(Path('.env'))['DATABASE_URL'])")"
#   chmod +x apply_greenfield_remaining.sh
#   ./apply_greenfield_remaining.sh /path/to/DDL
#
# Does NOT run:
#   - 04_dealer_master.sql (legacy; use dealer_ref)
#   - 10_rto_payment_details.sql (legacy; use 10_rto_queue.sql)
#   - 04a/04b/22/25/26/27/28 (already applied)
#
# After this script succeeds, see Documentation/ddl-greenfield-apply-order.md for optional
# seed_oem_service_schedule.sql and Phase 2 (alter/*.sql).

set -euo pipefail

DDL_ROOT="${1:?Usage: $0 /path/to/DDL}"

run() {
  local f="$1"
  echo "=== $f ==="
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$DDL_ROOT/$f"
}

: "${DATABASE_URL:?Set DATABASE_URL}"

run 01_ai_reader_queue.sql
run 02_customer_master.sql
run 03_vehicle_master.sql
run 04c_oem_service_schedule.sql
run 05_sales_master.sql
run 06_insurance_master.sql
run 08_service_reminders_queue.sql
run 09_trigger_sales_master_sync_service_reminders.sql
run 10_rto_queue.sql
run 11_rc_status_sms_queue.sql
run 12_bulk_loads.sql
run 18_vehicle_inventory_master.sql
run 19_challan_staging.sql
run 20_challan_master.sql
run 21_challan_details.sql
run 23_challan_master_staging.sql
run 24_challan_details_staging.sql

echo "=== apply_greenfield_remaining: OK ==="
