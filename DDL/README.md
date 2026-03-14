# DDL Scripts (PostgreSQL)

All Postgres DDL for the **auto_ai** database. Run in order when creating a fresh schema.

## Order

1. `01_ai_reader_queue.sql` — no dependencies  
2. `02_customer_master.sql`  
3. `03_vehicle_master.sql`  
4. `04a_oem_ref.sql`  
5. `04b_dealer_ref.sql` — requires oem_ref  
6. `04c_oem_service_schedule.sql` — requires oem_ref  
7. `05_sales_master.sql` — requires customer_master, vehicle_master, dealer_ref  
8. `08_service_reminders_queue.sql` — requires customer_master, vehicle_master  

## Run (examples)

```bash
# Using psql (with pgpass or -W for password)
psql -h localhost -U postgres -d auto_ai -f DDL/01_ai_reader_queue.sql
psql -h localhost -U postgres -d auto_ai -f DDL/02_customer_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/03_vehicle_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/04a_oem_ref.sql
psql -h localhost -U postgres -d auto_ai -f DDL/04b_dealer_ref.sql
psql -h localhost -U postgres -d auto_ai -f DDL/04c_oem_service_schedule.sql
psql -h localhost -U postgres -d auto_ai -f DDL/05_sales_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/08_service_reminders_queue.sql
```

Or run all in order (Unix):

```bash
for f in DDL/0*.sql; do psql -h localhost -U postgres -d auto_ai -f "$f"; done
```

## Alter / migrations

One-off changes (e.g. new columns) go in **`DDL/alter/`**. Run against an existing database as needed.

- `01a_ai_reader_queue_add_classification.sql` — adds `document_type`, `classification_confidence` for the two-step (classify + OCR) pipeline.
- `02a_customer_master_add_file_location.sql` — adds `file_location` to customer_master.
- `02b_customer_master_customer_id_pk.sql` — adds `customer_id` as PK, aadhar last 4 only, unique (aadhar, phone); migrates sales_master to customer_id FK.
- `02c_customer_master_add_gender_dob.sql` — adds `gender`, `date_of_birth` to customer_master (for QR/Aadhar granular data).
- `03a_vehicle_master_add_model_colour.sql` — adds `model` and `colour` (VARCHAR 64) to vehicle_master.
- `04b_rename_dealer_master_to_dealer_ref_and_oem.sql` — creates `oem_ref`, renames `dealer_master` to `dealer_ref`, replaces `dealer_of` with `oem_id` (FK to oem_ref).
- `04c_dealer_ref_add_auto_sms_reminders.sql` — adds `auto_sms_reminders` (Y/N) to dealer_ref.
- `04d_drop_oem_service_frequency_add_oem_service_schedule.sql` — drops `oem_service_frequency`, creates `oem_service_schedule`.
- `08a_service_reminders_queue_add_reminder_date.sql` — adds `reminder_date` to service_reminders_queue.
- `08b_service_reminders_queue_add_reminder_type_dealer_id.sql` — adds `reminder_type`, `dealer_id` to service_reminders_queue.
- `04f_oem_service_schedule_add_reminder_type.sql` — adds `reminder_type` to oem_service_schedule; set to SMS for existing rows.
- `04g_oem_ref_add_dms_link.sql` — adds `dms_link` (VARCHAR 512) to oem_ref; app uses dealer → oem_id → dms_link when opening DMS tab.

## Maintenance

- Keep this folder in sync with **Documentation/Database DDL.md**.
- When adding/removing/altering tables: add or update the corresponding `NN_name.sql` script (and any `alter/` script) and the doc.
