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
8. `08_service_reminders_queue.sql` — requires sales_master  
9. `09_trigger_sales_master_sync_service_reminders.sql` — trigger on sales_master  
10. `10_rto_payment_details.sql` — requires sales_master; current deployments then run `DDL/alter/12c_rename_rto_payment_details_to_rto_queue.sql`
11. `11_rc_status_sms_queue.sql` — requires current `rto_queue` schema  
12. `18_vehicle_inventory_master.sql` — requires `dealer_ref`  
13. `19_challan_staging.sql` — requires `dealer_ref`  
14. `20_challan_master.sql` — requires `dealer_ref`  
15. `21_challan_details.sql` — requires `challan_master` and `vehicle_inventory_master`  
16. `22_subdealer_discount_master.sql` — requires `dealer_ref`  

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
psql -h localhost -U postgres -d auto_ai -f DDL/09_trigger_sales_master_sync_service_reminders.sql
psql -h localhost -U postgres -d auto_ai -f DDL/10_rto_payment_details.sql
psql -h localhost -U postgres -d auto_ai -f DDL/alter/12c_rename_rto_payment_details_to_rto_queue.sql
psql -h localhost -U postgres -d auto_ai -f DDL/11_rc_status_sms_queue.sql
psql -h localhost -U postgres -d auto_ai -f DDL/18_vehicle_inventory_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/19_challan_staging.sql
psql -h localhost -U postgres -d auto_ai -f DDL/20_challan_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/21_challan_details.sql
psql -h localhost -U postgres -d auto_ai -f DDL/22_subdealer_discount_master.sql
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
- `02j_customer_master_add_care_of.sql` — adds `care_of` (Aadhaar QR care-of / father–husband) for DMS and Form 20.
- `02k_customer_master_add_dms_contact_id.sql` — adds optional **`dms_contact_id`** (Siebel Contact Id) to `customer_master`.
- `03a_vehicle_master_add_model_colour.sql` — adds `model` and `colour` (VARCHAR 64) to vehicle_master.
- `03i_vehicle_master_unique_engine_chassis.sql` — vehicle_master: add unique index on (engine, chassis) when both are non-empty.
- `04b_rename_dealer_master_to_dealer_ref_and_oem.sql` — creates `oem_ref`, renames `dealer_master` to `dealer_ref`, replaces `dealer_of` with `oem_id` (FK to oem_ref).
- `04c_dealer_ref_add_auto_sms_reminders.sql` — adds `auto_sms_reminders` (Y/N) to dealer_ref.
- `04h_dealer_ref_add_rto_name.sql` — adds `rto_name` (VARCHAR 128) to `dealer_ref` for insurance/Vahan RTO label; seeds `RTO-Bharatpur` for `dealer_id = 100001`. **Required** if Fill Insurance errors on `dr.rto_name` does not exist.
- `04d_drop_oem_service_frequency_add_oem_service_schedule.sql` — drops `oem_service_frequency`, creates `oem_service_schedule`.
- `08a_service_reminders_queue_add_reminder_date.sql` — adds `reminder_date` to service_reminders_queue.
- `08b_service_reminders_queue_add_reminder_type_dealer_id.sql` — adds `reminder_type`, `dealer_id` to service_reminders_queue.
- `04f_oem_service_schedule_add_reminder_type.sql` — adds `reminder_type` to oem_service_schedule; set to SMS for existing rows.
- `04g_oem_ref_add_dms_link.sql` — adds `dms_link` (VARCHAR 512) to oem_ref; app uses dealer → oem_id → dms_link when opening DMS tab.
- `05a_sales_master_add_dealer_unique.sql` — adds UNIQUE (customer_id, vehicle_id, dealer_id) for FK from rto_payment_details and service_reminders_queue. (Superseded by 05b–05d.)
- `05b_sales_master_add_sales_id_pk.sql` — adds sales_id as PK to sales_master; drops composite FKs from rto_payment_details and service_reminders_queue.
- `05c_rto_payment_details_add_sales_id_fk.sql` — rto_payment_details: add sales_id, FK to sales_master(sales_id). Run after 05b.
- `05d_service_reminders_queue_add_sales_id_fk.sql` — service_reminders_queue: add sales_id, FK to sales_master(sales_id). Run after 05b.
- `09a_trigger_sales_master_use_sales_id.sql` — updates trigger to use sales_id for service_reminders_queue. Run after 05b, 05d.
- `11a_rc_status_sms_queue_dealer_fk_via_sales.sql` — rc_status_sms_queue: add sales_id, dealer_id FK via sales_master. Run after 05b, 05c.
- `11b_rc_status_sms_queue_dealer_fk_via_rto_queue.sql` — rc_status_sms_queue: sync dealer_id from rto_queue and enforce FK (sales_id, dealer_id) → rto_queue(sales_id, dealer_id). Run after 11a and 12c.
- `06b_insurance_master_fk_to_sales_only.sql` — insurance_master: FK to sales_master only (drops FKs to customer_master, vehicle_master).
- `08c_service_reminders_queue_fk_to_sales_only.sql` — service_reminders_queue: composite FK to sales_master (customer_id, vehicle_id, dealer_id). (Superseded by 05b–05d.)
- `10d_rto_payment_details_fk_dealer_via_sales.sql` — rto_payment_details: composite FK to sales_master (customer_id, vehicle_id, dealer_id). (Superseded by 05b–05c.)
- `12c_rename_rto_payment_details_to_rto_queue.sql` — renames the live RTO work table to `rto_queue`; current Add Sales writes queue rows here instead of auto-running dummy Vahan.
- `12e_rto_queue_batch_processing.sql` — adds dealer-batch lease/progress columns and indexes so the RTO Queue page can process the oldest 7 rows in one browser session per dealer.
- `05f_sales_master_add_rto_scrape_fields.sql` — adds `sales_master.vahan_application_id` and `sales_master.rto_charges` so RTO batch scrapes are retained and overwritten on retry.
- `05g_drop_vehicle_master_rto_scrape_fields.sql` — drops the deprecated `vehicle_master.vahan_application_id` and `vehicle_master.rto_charges` columns after storage moved to `sales_master`.
- `10j_form_insurance_view.sql` — creates **`form_insurance_view`** (Hero Insurance: chassis, customer, nominee, insurer from existing master columns per sale).
- `12i_insurance_master_drop_insurance_automation_completed.sql` — drops **`insurance_automation_completed`** if present (superseded; Add Sales uses **`insurance_master.policy_num`** for Generate Insurance eligibility via `GET /add-sales/create-invoice-eligibility`).
- `13a_add_sales_staging.sql` — creates **`add_sales_staging`** (UUID **`staging_id`**, **`dealer_id`**, **`payload_json`**, **`status`**). **Draft** rows are written on **`POST /submit-info`** (staging only); masters commit after successful **Create Invoice**; **Create Invoice** uses **`staging_id`** to load **`payload_json`**. Run after **`dealer_ref`** exists.
- `13b_drop_form_dms_view.sql` — drops **`form_dms_view`**; DMS fill uses **`backend/app/repositories/form_dms.py`** (inline join) and future **`add_sales_staging.payload_json`** (OCR merge).
- `14a_nominee_gender_insurance_drop_customer_legacy.sql` — **`insurance_master.nominee_gender`**; drops legacy **`customer_master`** nominee / father–husband columns.
- `14b_insurance_master_add_insurance_cost.sql` — adds **`insurance_cost`** (total premium: preview before **Issue Policy**, then refreshed from post–**Issue Policy** scrape on **Generate Insurance**).
- `14c_insurance_master_drop_insurance_cost.sql` — drops **`insurance_cost`**; use **`premium`** only (preview scrape for **policy_num**, **policy_from**, **policy_to**, **premium**, **idv**).
- `15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql` — **`vehicle_master.variant`**; widen **`place_of_registeration`** to 128; partial unique index on **`chassis`** (VIN); drop **`dms_sku`**.
- `16a_dealer_ref_prefer_insurer_form_insurance_view.sql` — **`dealer_ref.prefer_insurer`**; recreates **`form_insurance_view`**.
- `17a_dealer_ref_hero_cpi_form_insurance_view.sql` — **`dealer_ref.hero_cpi`** (**Y**/**N**, default **N**); recreates **`form_insurance_view`** with **`hero_cpi`**.
- `18a_challan_master_add_order_invoice_totals.sql` — **`challan_master`**: **`order_number`**, **`invoice_number`**, **`total_ex_showroom_price`**, **`total_discount`**.
- `18b_vehicle_inventory_master_add_discount.sql` — **`vehicle_inventory_master`**: **`discount`**.

**New table (run after customer_master exists):**
- `10_rto_payment_details.sql` — legacy base creation for the RTO table; current schema then applies `12c_rename_rto_payment_details_to_rto_queue.sql` so the active table is `rto_queue`.

## Maintenance

- Keep this folder in sync with **Documentation/Database DDL.md**.
- When adding/removing/altering tables: add or update the corresponding `NN_name.sql` script (and any `alter/` script) and the doc.
