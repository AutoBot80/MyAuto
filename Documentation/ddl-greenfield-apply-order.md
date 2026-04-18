# Greenfield DDL apply order (RDS)

**Purpose:** Create the **remaining** tables after the `*_ref` tables and `master_ref` already exist on RDS.

## Already on your DB (skip)

`dealer_ref`, `oem_ref`, `roles_ref`, `login_ref`, `login_roles_ref`, `master_ref`, `subdealer_discount_master_ref`.

## Phase 1 — base `DDL/*.sql` (one script)

From repo: **`DDL/apply_greenfield_remaining.sh`**

```bash
export DATABASE_URL="$(cd /opt/saathi/backend && /opt/saathi/venv/bin/python3 -c \
  "from pathlib import Path; from dotenv import dotenv_values; print(dotenv_values(Path('.env'))['DATABASE_URL'])")"

chmod +x /tmp/DDL/apply_greenfield_remaining.sh
/tmp/DDL/apply_greenfield_remaining.sh /tmp/DDL
```

Or run files manually in this **exact** order:

| Order | File |
|------:|------|
| 1 | `01_ai_reader_queue.sql` |
| 2 | `02_customer_master.sql` |
| 3 | `03_vehicle_master.sql` |
| 4 | `04c_oem_service_schedule.sql` |
| 5 | `05_sales_master.sql` |
| 6 | `06_insurance_master.sql` |
| 7 | `08_service_reminders_queue.sql` |
| 8 | `09_trigger_sales_master_sync_service_reminders.sql` |
| 9 | `10_rto_queue.sql` |
| 10 | `11_rc_status_sms_queue.sql` |
| 11 | `12_bulk_loads.sql` |
| 12 | `18_vehicle_inventory_master.sql` |
| 13 | `19_challan_staging.sql` |
| 14 | `20_challan_master.sql` |
| 15 | `21_challan_details.sql` |
| 16 | `23_challan_master_staging.sql` |
| 17 | `24_challan_details_staging.sql` |

**Do not run on a greenfield DB:**

- `04_dealer_master.sql` — superseded by `dealer_ref`
- `10_rto_payment_details.sql` — old name; use `10_rto_queue.sql`

## Optional seed (after `04c_oem_service_schedule.sql`)

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/DDL/seed_oem_service_schedule.sql
```

Hero service schedule rows for `oem_id = 1` (matches typical OEM seed).

## Phase 2 — `DDL/alter/*.sql` (parity with dev)

The base files above are **minimal** table shells. The app in git often expects **extra columns, views, and staging** from `DDL/alter/`.

- **Do not** blindly run every alter on an empty DB: some scripts are for **renaming** or **migrating** old schemas (e.g. `DDL/alter/24a_rto_queue_schema_redesign.sql` is for upgrades, not greenfield — see `Documentation/Database DDL.md`).
- Safer approaches:
  1. **pg_dump** schema from dev `auto_ai` and restore to RDS, **or**
  2. Run alters **selectively** in numeric order, **skipping** migration-only scripts after reading each header, **or**
  3. Work with a short list of **required** alters for features you use next (e.g. `13a_add_sales_staging.sql` for Add Sales staging).

If an alter fails, read the error: often the table already matches the target state (`IF NOT EXISTS` / `IF EXISTS` guards vary).

## Verify

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
```
