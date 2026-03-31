# Database DDL
## auto_ai (PostgreSQL)

This document lists the current database tables and their columns. **Executable DDL scripts** are in the **`DDL/`** folder (e.g. `DDL/01_ai_reader_queue.sql`). Keep both this doc and the `DDL/` scripts updated when adding, removing, or altering tables.

**Date format:** The default date format for the application and database is **dd/mm/yyyy** (e.g. 30/05/1980). Use this format for all date fields (e.g. `date_of_birth`) in the app and in the DB.

---

## 1) `ai_reader_queue`

**Purpose:** Queue for OCR/AI reader processing of uploaded scans.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('ai_reader_queue_id_seq'::regclass)` | Primary key |
| `subfolder` | `text` | NO |  | Upload subfolder (e.g. `1234_1103`) |
| `filename` | `text` | NO |  | Saved filename |
| `status` | `text` | NO | `'queued'::text` | e.g. `queued`, `processing`, `done`, `failed` |
| `document_type` | `varchar(64)` | YES |  | Step 1: AI classification (e.g. Aadhar card, Driving license) |
| `classification_confidence` | `real` | YES |  | Confidence 0–1 from classifier |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |
| `updated_at` | `timestamptz` | NO | `now()` | Updated timestamp |

**Primary key:** `ai_reader_queue_pkey` on (`id`)

---

## 2) `customer_master`

**Purpose:** Customer master data.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `customer_id` | `integer` | NO | `nextval('customer_master_customer_id_seq'::regclass)` | Primary key |
| `aadhar` | `char(4)` | NO |  | Last 4 digits of Aadhar only (full number shown on frontend only; DB stores last 4 for compliance) |
| `name` | `text` | NO |  | Customer name |
| `address` | `text` | YES |  | Address (constructed from care of, house, street, location when saving from QR) |
| `pin` | `char(6)` | YES |  | PIN code |
| `city` | `text` | YES |  | City |
| `state` | `text` | YES |  | State |
| `mobile_number` | `integer` | YES |  | Customer mobile number (10 digits) |
| `alt_phone_num` | `varchar(16)` | YES |  | Alternate / landline customer number (Sales Detail Sheet `Alternate`) |
| `profession` | `varchar(16)` | YES |  | Customer profession (e.g. Service, Business) |
| `financier` | `varchar(255)` | YES |  | Financier name captured from details sheet / insurance context |
| `marital_status` | `varchar(32)` | YES |  | Customer marital status captured from details sheet |
| `care_of` | `varchar(255)` | YES |  | Care of / father–husband from **Aadhaar QR**; sole source for DMS Father/Husband line and Form 20 |
| `dms_relation_prefix` | `varchar(8)` | YES |  | DMS relation line: app persists **first 3 characters of trimmed address** when length ≥ 3, else **`D/o`** (female) / **`S/o`** (otherwise); see `app/services/dms_relation_prefix.py` |
| `dms_contact_path` | `varchar(16)` | NO | `'found'` | `found` / `new_enquiry` / `skip_find`: dummy uses `skip_find` to skip finder Go; **real Siebel ignores `skip_find` for ordering** — always Contact Find (`DMS_REAL_URL_CONTACT`) first, then linear SOP per LLD §2.4d |
| `dms_contact_id` | `varchar(128)` | YES |  | DMS / Siebel **Contact Id** from automation scrape (`DDL/alter/02k_customer_master_add_dms_contact_id.sql`) |
| `file_location` | `text` | YES |  | Legacy mirror of per-sale folder; **canonical** folder for a sale is `sales_master.file_location` (kept in sync on master commit) |
| `gender` | `varchar(8)` | YES |  | Gender from Aadhar QR (e.g. M, F) |
| `date_of_birth` | `varchar(20)` | YES |  | Date of birth (dd/mm/yyyy); default date format for app and DB |

**Primary key:** `customer_master_pkey` on (`customer_id`)

**Unique:** `uq_customer_aadhar_mobile` on (`aadhar`, `mobile_number`) — customer identified by last 4 Aadhar + mobile

---

## 3) `vehicle_master`

**Purpose:** Vehicle master data. Used by Form 20, sales, insurance.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `vehicle_id` | `integer` | NO | `nextval('vehicle_master_vehicle_id_seq'::regclass)` | Primary key |
| `key_num` | `varchar(32)` | YES |  | Key number; on first master commit equals **`raw_key_num`** |
| `engine` | `varchar(64)` | YES |  | Engine number |
| `chassis` | `varchar(64)` | YES |  | Chassis number |
| `battery` | `varchar(64)` | YES |  | Battery number |
| `plate_num` | `varchar(32)` | YES |  | Plate number |
| `model` | `varchar(64)` | YES |  | Vehicle model |
| `colour` | `varchar(64)` | YES |  | Vehicle colour |
| `raw_frame_num` | `varchar(32)` | YES |  | Raw frame/chassis from Submit Info / detail sheet (Fill DMS merge does not overwrite) |
| `raw_engine_num` | `varchar(32)` | YES |  | Raw engine from Submit Info / detail sheet (Fill DMS merge does not overwrite) |
| `raw_key_num` | `varchar(32)` | YES |  | Raw extracted key number |
| `year_of_mfg` | `integer` | YES |  | Year of manufacture (yyyy) |
| `cubic_capacity` | `numeric(10,2)` | YES |  | Cubic capacity (cc) |
| `body_type` | `varchar(16)` | YES |  | Body type (e.g. Sedan, SUV) |
| `seating_capacity` | `integer` | YES |  | Seating capacity |
| `place_of_registeration` | `varchar(128)` | YES |  | From **`dealer_ref.rto_name`** for the sale’s dealer (via latest `sales_master`); widened in **`DDL/alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql`** |
| `oem_name` | `varchar(64)` | YES |  | From **`oem_ref.oem_name`** via sale’s **`dealer_ref.oem_id`**; Form 20 field 16 |
| `variant` | `varchar(64)` | YES |  | Variant from Siebel Vehicles page scrape |
| `vehicle_type` | `varchar(32)` | YES |  | Type of vehicle; stored **ALL CAPS** after DMS merge (normalized from mixed-case Siebel) |
| `num_cylinders` | `integer` | YES |  | Number of cylinders |
| `length_mm` | `integer` | YES |  | Length in mm |
| `fuel_type` | `varchar(16)` | YES |  | Fuel type (e.g. Petrol, Diesel) |
| `vehicle_ex_showroom_price` | `numeric(12,2)` | YES |  | **Ex-showroom / Order Value** from DMS (e.g. after **Price All / Allocate All** in booking attach); `form_vahan_view` exposes it as `vehicle_price` for Vahan |

**Primary key:** `vehicle_master_pkey` on (`vehicle_id`)

**Unique:** `uq_vehicle_raw_triple` on (`raw_frame_num`, `raw_engine_num`, `raw_key_num`)
**Unique:** `uq_vehicle_engine_chassis` on (`engine`, `chassis`) (only when both are non-empty)
**Unique (partial):** `uq_vehicle_master_chassis_nonempty` on `UPPER(BTRIM(chassis))` where `chassis` is non-null and non-blank — canonical VIN after scrape (`DDL/alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql`). **`dms_sku`** removed by the same script.

---

## 4) `sales_master`

**Purpose:** Sales master linking customer and vehicle. One row per (customer, vehicle). sales_id is PK; used by `rto_queue` and `service_reminders_queue`.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `sales_id` | `integer` | NO | `nextval('sales_master_sales_id_seq'::regclass)` | Primary key (auto-generated) |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `billing_date` | `timestamptz` | NO | `now()` | System date/time |
| `dealer_id` | `integer` | YES |  | FK → `dealer_ref(dealer_id)` |
| `file_location` | `varchar(128)` | YES |  | Per-sale upload/OCR subfolder (`DDL/alter/05e_sales_master_add_file_location.sql`) |
| `order_number` | `varchar(128)` | YES |  | DMS **Order#** — scraped during the **order** stage of the DMS run (`update_sales_master_from_dms_scrape`; `DDL/alter/05h_…`) |
| `invoice_number` | `varchar(128)` | YES |  | DMS **Invoice#** — scraped during the **invoice** stage (`05h_…`) |
| `enquiry_number` | `varchar(128)` | YES |  | DMS **Enquiry#** — scraped during the **enquiry** stage (`05i_…`) |
| `vahan_application_id` | `varchar(128)` | YES |  | Filled by **Vahan** / RTO queue processing when the application id is scraped — **not** by DMS (`DDL/alter/05f_…`) |
| `rto_charges` | `numeric(12,2)` | YES |  | Filled by **Vahan** / RTO queue processing when fees are scraped — **not** by DMS (`05f_…`) |

**Primary key:** `sales_master_pkey` on (`sales_id`)

**Unique:** `uq_sales_customer_vehicle` on (`customer_id`, `vehicle_id`) — post–Create Invoice commit **inserts** a new row only; **duplicate pair fails** (no `ON CONFLICT` upsert on `sales_master`).

**Foreign keys:**
- `fk_sales_customer`: (`customer_id`) → `customer_master(customer_id)`
- `fk_sales_dealer`: (`dealer_id`) → `dealer_ref(dealer_id)`
- `fk_sales_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`

---

## 5) `oem_ref`

**Purpose:** OEM / brand reference (e.g. Hero Motors).

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `oem_id` | `integer` | NO | `nextval('oem_ref_oem_id_seq'::regclass)` | Primary key (auto-generated) |
| `oem_name` | `varchar(255)` | YES |  | OEM or brand name |
| `vehicles_type` | `varchar(128)` | YES |  | Type of vehicles (e.g. 2W, 4W) |
| `dms_link` | `varchar(512)` | YES |  | URL to OEM DMS; app uses dealer → oem_id → this link when opening DMS tab |

**Primary key:** `oem_ref_pkey` on (`oem_id`)

---

## 5a) `oem_service_schedule`

**Purpose:** OEM service schedule (service number, type, days from billing, active flag). Used by trigger to populate service_reminders_queue.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `oem_id` | `integer` | NO |  | FK → `oem_ref(oem_id)` |
| `service_num` | `integer` | YES |  | Service sequence number |
| `service_type` | `varchar(16)` | YES |  | Free or Paid |
| `days_from_billing` | `integer` | YES |  | Days from billing date for this service |
| `active_flag` | `char(1)` | YES |  | Y or N |
| `reminder_type` | `varchar(16)` | YES |  | e.g. SMS, Email; flows to service_reminders_queue |

**Foreign keys:**
- `fk_oem_service_schedule_oem`: (`oem_id`) → `oem_ref(oem_id)`

**Checks:**
- `chk_oem_service_schedule_service_type` — `service_type` IN ('Free', 'Paid')
- `chk_oem_service_schedule_active_flag` — `active_flag` IN ('Y', 'N')

---

## 6) `dealer_ref`

**Purpose:** Dealer reference; replaces dealer_master. Used by Form 20 (field 10 dealer name), sales_master.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `dealer_id` | `integer` | NO | `nextval('dealer_ref_dealer_id_seq'::regclass)` | Primary key |
| `dealer_name` | `varchar(255)` | NO |  | Dealer name |
| `oem_id` | `integer` | YES |  | FK → `oem_ref(oem_id)`; supplied on insert, not auto-generated |
| `address` | `text` | YES |  | Address |
| `pin` | `char(6)` | YES |  | PIN code |
| `city` | `text` | YES |  | City |
| `state` | `text` | YES |  | State |
| `rto_name` | `varchar(128)` | YES |  | Dealer-mapped RTO office name (e.g. `RTO-Bharatpur`) |
| `parent_id` | `integer` | YES |  | Parent dealer id (hierarchy) |
| `phone` | `varchar(16)` | YES |  | Phone (up to 16 digits) |
| `auto_sms_reminders` | `char(1)` | YES |  | Y or N; when Y, trigger populates service_reminders_queue on sales_master upsert |

**Primary key:** `dealer_ref_pkey` on (`dealer_id`)

**Check:** `chk_dealer_ref_auto_sms_reminders` — `auto_sms_reminders` IN ('Y', 'N')

**Foreign keys:**
- `fk_dealer_ref_oem`: (`oem_id`) → `oem_ref(oem_id)`

---

## 7) `insurance_master`

**Purpose:** Insurance policy records linked to customer and vehicle. Unique per (customer, vehicle, insurance_year).

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `insurance_id` | `integer` | NO | `nextval('insurance_master_insurance_id_seq'::regclass)` | Primary key (auto-generated) |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `insurance_year` | `integer` | YES |  | Policy year (yyyy) |
| `idv` | `numeric(12,2)` | YES |  | Insured declared value |
| `insurer` | `varchar(255)` | YES |  | Insurer name |
| `policy_num` | `varchar(24)` | YES |  | Policy number |
| `policy_from` | `date` | YES |  | Policy start (display dd/mm/yyyy) |
| `policy_to` | `date` | YES |  | Policy end (display dd/mm/yyyy) |
| `nominee_name` | `text` | YES |  | Nominee name |
| `nominee_age` | `integer` | YES |  | Nominee age |
| `nominee_relationship` | `varchar(64)` | YES |  | Nominee relationship |
| `nominee_gender` | `varchar(16)` | YES |  | Nominee gender (details sheet / OCR); lives in staging until **Generate Insurance** commits (`DDL/alter/10j_form_insurance_view.sql`, `DDL/alter/14a_nominee_gender_insurance_drop_customer_legacy.sql`) |
| `policy_broker` | `varchar(255)` | YES |  | Policy broker |
| `premium` | `numeric(12,2)` | YES |  | Premium amount |
| `insurance_cost` | `numeric(12,2)` | YES |  | Total premium / payable amount: initial value from **policy preview** before **Issue Policy**, refreshed after **Issue Policy** from a second scrape (**`DDL/alter/14b_insurance_master_add_insurance_cost.sql`**) |

**Primary key:** `insurance_master_pkey` on (`insurance_id`)

**Unique:** `uq_insurance_customer_vehicle_year` on (`customer_id`, `vehicle_id`, `insurance_year`)

**Foreign keys:**
- `fk_insurance_customer`: (`customer_id`) → `customer_master(customer_id)`
- `fk_insurance_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`

*Note: Alter 06b optionally changes FK to sales_master (customer_id, vehicle_id) for stricter referential integrity.*

---

## 8) `service_reminders_queue`

**Purpose:** Queue of service reminders per customer/vehicle. Populated **only** by trigger `fn_sales_master_sync_service_reminders` when `sales_master` is inserted or updated and the dealer has `auto_sms_reminders = Y` (`DDL/09_trigger_sales_master_sync_service_reminders.sql`). **Application code must not** INSERT or UPDATE this table — use `sales_master` changes so the trigger remains the single source of reminder rows.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('service_reminders_queue_id_seq'::regclass)` | Primary key (auto-generated) |
| `sales_id` | `integer` | NO |  | FK → `sales_master(sales_id)` |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `billing_date` | `date` | YES |  | Billing/sale date |
| `service_date` | `date` | YES |  | Scheduled service date |
| `service_type` | `varchar(16)` | YES |  | Free or Paid |
| `reminder_num` | `integer` | YES |  | Trigger inserts one row per schedule with reminder_num 1 |
| `reminder_date` | `date` | YES |  | Date when this reminder should be sent (e.g. 15 days before service_date) |
| `reminder_type` | `varchar(16)` | YES |  | From oem_service_schedule (e.g. SMS) |
| `reminder_status` | `varchar(16)` | YES |  | Status of reminder |
| `dealer_id` | `integer` | YES |  | FK → `dealer_ref(dealer_id)` |

**Primary key:** `service_reminders_queue_pkey` on (`id`)

**Foreign keys:**
- `fk_service_reminders_sales`: (`sales_id`) → `sales_master(sales_id)`

**Check:** `chk_service_reminders_service_type` — `service_type` IN ('Free', 'Paid')

---

## 9) `rto_queue`

**Purpose:** RTO work queue for a sale. Populated when Fill Forms completes DMS/Form 20 work; rows start in `Queued`/`Pending` and can later be claimed in dealer-scoped oldest-first batches that run Vahan up to the upload/cart checkpoint before any payment details are added.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `application_id` | `varchar(128)` | NO |  | Primary key; stable queue/reference id |
| `sales_id` | `integer` | NO |  | FK → `sales_master(sales_id)`; UNIQUE |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `dealer_id` | `integer` | YES |  | Dealer reference |
| `name` | `varchar(255)` | YES |  | Customer name (denormalized) |
| `mobile` | `varchar(16)` | YES |  | Customer mobile |
| `chassis_num` | `varchar(64)` | YES |  | Chassis number |
| `vahan_application_id` | `varchar(128)` | YES |  | Real Vahan application number once the dealer batch reaches upload/cart |
| `register_date` | `date` | NO | `CURRENT_DATE` | Date row added |
| `rto_fees` | `numeric(12,2)` | NO |  | Estimated or final RTO fees; overwritten with the latest Vahan-scraped amount on successful retry |
| `status` | `varchar(32)` | NO | `'Queued'` | e.g. Queued, In Progress, Added To Cart, Failed, Paid |
| `pay_txn_id` | `varchar(64)` | YES |  | Transaction ID when paid |
| `operator_id` | `varchar(64)` | YES |  | POS / operator identifier |
| `payment_date` | `date` | YES |  | Date paid (when status = Paid) |
| `rto_status` | `varchar(32)` | NO | `'Pending'` | RTO work status |
| `subfolder` | `varchar(128)` | YES |  | Upload subfolder for this sale |
| `processing_session_id` | `varchar(128)` | YES |  | Dealer batch/session id that claimed the row |
| `worker_id` | `varchar(128)` | YES |  | Worker/browser identifier for the active batch |
| `leased_until` | `timestamptz` | YES |  | Lease timeout for claimed queue rows |
| `attempt_count` | `integer` | NO | `0` | How many times the row was claimed for processing |
| `last_error` | `text` | YES |  | Latest processing failure text |
| `started_at` | `timestamptz` | YES |  | Time the Vahan batch started this row |
| `uploaded_at` | `timestamptz` | YES |  | Time the row reached the upload/cart checkpoint |
| `finished_at` | `timestamptz` | YES |  | Time the latest batch attempt completed |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |
| `updated_at` | `timestamptz` | NO | `now()` | Last change timestamp |

**Primary key:** `rto_queue_pkey` on (`application_id`)

**Unique:** `uq_rto_sales_id` on (`sales_id`), `uq_rto_customer_vehicle` on (`customer_id`, `vehicle_id`)

**Foreign keys:**
- `fk_rto_sales`: (`sales_id`) → `sales_master(sales_id)`

---

## 9a) `form_vahan_view`

**Purpose:** Read-only view that projects one sale into Vahan-friendly field labels so operators can inspect what the Vahan step will use without manually joining `sales_master`, `customer_master`, `vehicle_master`, `insurance_master`, and `rto_queue`.

**Primary keys / grain:** One row per submitted `(customer_id, vehicle_id)` sale, sourced from `sales_master` and enriched with the latest insurance and RTO rows when present.

**Important columns:**
- Technical/source columns: `sales_id`, `customer_id`, `vehicle_id`, `dealer_id`, `subfolder`, `queue_id`, `application_id`, `pay_txn_id`, `rto_payment_status`, `rto_fees`, `rto_dealer_id`, `vehicle_model`, `vehicle_colour`, `fuel_type`, `year_of_mfg`, `vehicle_price`.
- Label-aligned columns: `"Registration Type *"`, `"Chassis No *"`, `"Engine/Motor No (Last 5 Chars)"`, `"Purchase Delivery Date"`, `"Owner Name *"`, `"Category *"`, `"Mobile No"`, `"Permanent Address"`, `"Insurance Type"`, `"Insurer"`, `"Policy No"`, `"Application No"`, `"Assigned Office & Action"`, `"Amount"`, and related Vahan labels used by the dummy site/export file.

**Operational notes:**
- The view uses the latest `insurance_master` row per `(customer_id, vehicle_id)` by `insurance_year`.
- The view uses the latest `rto_queue` row per `(customer_id, vehicle_id)` by `created_at`.
- `queue_id` is the stable `rto_queue.application_id`; `application_id` / `"Application No"` switch to `rto_queue.vahan_application_id` once the batch reaches the Vahan cart/upload checkpoint.
- `vehicle_price` in the view is sourced from `vehicle_master.vehicle_ex_showroom_price`, populated by Fill DMS (`update_vehicle_master_from_dms`) so Vahan automation can read only from `form_vahan_view`.
- Successful RTO batch runs overwrite the latest scraped Vahan application id / RTO charges in both `rto_queue` and `sales_master`; session-expiry failures return the queue row to `Pending` without clearing previously scraped values.

---

## 9b) DMS fill source (former `form_dms_view`)

**Removed:** The PostgreSQL view **`form_dms_view`** is dropped by **`DDL/alter/13b_drop_form_dms_view.sql`**. Historical DDL that created it remains under **`DDL/alter/10f`–`10i_*.sql`** for reference only.

**Current behavior:** Create Invoice (**Fill DMS**) builds the same label-aligned row in application code:

- **Legacy path (after Submit Info):** `backend/app/repositories/form_dms.py` runs an **inline** `SELECT` over `sales_master` + `customer_master` + `vehicle_master` + `dealer_ref` (equivalent to the old view).
- **Target path:** values come from **`add_sales_staging.payload_json`** — merged OCR extraction and operator corrections — without requiring master rows or a SQL view first (**LLD §2.2a**).

`ocr_output/<subfolder>/DMS_Form_Values.txt` records the runtime values Playwright sent (**BR-10**).

---

## 9c) `form_insurance_view`

**Purpose:** Read-only view for Hero/MISP automation: one row per sale (`sales_master`) with `customer_master`, `vehicle_master`, `dealer_ref` / `oem_ref`, and the **latest** `insurance_master` row per `(customer_id, vehicle_id)` (order: `policy_to`, `insurance_year`, `insurance_id`).

**Script:** `DDL/alter/10j_form_insurance_view.sql`.

**Important columns:** Chassis/frame (`frame_no`, `full_chassis`), engine, model, proposer and address fields, `insurer`, nominee columns, `financer_name`, `rto_name`, etc. — **only** columns that already exist on the base tables. Proposal-only UI defaults (email, add-ons, CPA, payment mode, registration date) remain **hardcoded** in Playwright until optional future columns exist.

**Operational notes:** `load_latest_insurance_values` uses `SELECT * FROM form_insurance_view WHERE customer_id = ? AND vehicle_id = ?`. **`build_insurance_fill_values`** (`insurance_form_values.py`) uses that row **together with** **`add_sales_staging.payload_json`** when Add Sales passes **`staging_id`**: the view reflects committed masters after Create Invoice; **`payload_json`** holds the full OCR/operator merge so the pair is the **complete** insurance input set (**BR-20**). Insurer may still fall back to **`OCR_To_be_Used.json`** when view and staging lack it. Repository: **`fetch_staging_payload`** accepts **draft** or **committed** staging rows.

---

## 10) `rc_status_sms_queue`

**Purpose:** SMS queue for RC status notifications; populated when RTO payment is done.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('rc_status_sms_queue_id_seq'::regclass)` | Primary key |
| `sales_id` | `integer` | NO |  | FK → `sales_master(sales_id)` |
| `dealer_id` | `integer` | YES |  | Dealer; validated via rto_queue (sales_id, dealer_id) |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `customer_mobile` | `varchar(16)` | YES |  | Customer mobile for SMS |
| `message_type` | `varchar(64)` | NO |  | Message type |
| `sms_status` | `varchar(32)` | NO | `'Pending'` | e.g. Pending, Sent |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |

**Primary key:** `rc_status_sms_queue_pkey` on (`id`)

**Foreign keys:**
- `fk_rc_sales`: (`sales_id`) → `sales_master(sales_id)`
- `fk_rc_rto_sales_dealer`: (`sales_id`, `dealer_id`) → `rto_queue(sales_id, dealer_id)`
- `fk_rc_rto`: (`customer_id`, `vehicle_id`) → `rto_queue(customer_id, vehicle_id)`

---

## 11) `bulk_loads`

**Purpose:** Hot operational table for bulk upload ingest, queue lifecycle, worker state, terminal status, and operator corrective actions.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('bulk_loads_id_seq'::regclass)` | Primary key |
| `job_id` | `varchar(64)` | YES |  | Logical job identifier; unique after queue redesign |
| `parent_job_id` | `varchar(64)` | YES |  | Parent bulk job for multi-customer splits |
| `subfolder` | `varchar(128)` | NO |  | Logical subfolder or result subfolder |
| `file_name` | `varchar(256)` | YES |  | Source scan filename |
| `mobile` | `varchar(16)` | YES |  | Extracted or assigned mobile |
| `name` | `varchar(128)` | YES |  | Customer name when known |
| `folder_path` | `varchar(512)` | YES |  | Relative operational folder path |
| `result_folder` | `varchar(512)` | YES |  | Final `Success/`, `Error/`, or `Rejected scans/` folder |
| `status` | `varchar(32)` | NO | `'Processing'` | Dashboard status: `Processing`, `Success`, `Error`, `Rejected` |
| `job_status` | `varchar(32)` | NO | `'received'` | Queue lifecycle: `received`, `queued`, `processing`, `retry_pending`, terminal state |
| `processing_stage` | `varchar(64)` | YES |  | Stage marker such as `INGEST`, `QUEUED`, `PRE_OCR`, `PROCESSING`, `COMPLETE`, `ERROR`, `REJECTED` |
| `source_path` | `varchar(1024)` | YES |  | Current queued/processing file path |
| `source_token` | `varchar(512)` | YES |  | Deduplication token from source path + file metadata |
| `attempt_count` | `integer` | NO | `0` | Number of lease attempts |
| `leased_until` | `timestamptz` | YES |  | Lease expiry for worker recovery |
| `worker_id` | `varchar(128)` | YES |  | Worker currently or last handling the job |
| `error_code` | `varchar(64)` | YES |  | Machine-readable failure code |
| `error_message` | `text` | YES |  | Operator-visible failure reason |
| `action_taken` | `boolean` | NO | `false` | Operator corrected the `Error` or `Rejected` record |
| `dealer_id` | `integer` | YES |  | Dealer ownership / filter |
| `started_at` | `timestamptz` | YES |  | First worker start time |
| `finished_at` | `timestamptz` | YES |  | Terminal completion time |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |
| `updated_at` | `timestamptz` | NO | `now()` | Updated timestamp |

**Primary key:** `bulk_loads_pkey` on (`id`)

**Important indexes and constraints:**
- `idx_bulk_loads_job_id` — unique on (`job_id`)
- `idx_bulk_loads_dealer_source_token` — unique on (`dealer_id`, `source_token`) where `source_token` is not null
- `idx_bulk_loads_dealer_created_at_desc` — (`dealer_id`, `created_at DESC`)
- `idx_bulk_loads_dealer_status_created_at_desc` — (`dealer_id`, `status`, `created_at DESC`)
- `idx_bulk_loads_job_status_created_at_desc` — (`job_status`, `created_at DESC`)
- `idx_bulk_loads_leased_until` — (`leased_until`)
- `idx_bulk_loads_unresolved_hot` — (`dealer_id`, `updated_at DESC`) where `status IN ('Processing', 'Error', 'Rejected')`

**Operational note:** The current UI/API reads only from `bulk_loads`. Old `Error` and `Rejected` rows remain hot until `action_taken=true`.

---

## Table Usage Summary

| Table | Used by |
|-------|---------|
| `ai_reader_queue` | OCR service, uploads router |
| `customer_master` | Submit Info, customer search, Form 20, RTO |
| `vehicle_master` | Submit Info, Form 20, Fill DMS (update), Vahan vehicle_price source, RTO |
| `sales_master` | Submit Info, RTO, service reminders, insurance |
| `oem_ref` | dealer_ref, Form 20 (oem_name via dealer) |
| `oem_service_schedule` | Trigger for service_reminders_queue |
| `dealer_ref` | Form 20, sales_master, service reminders |
| `insurance_master` | Submit Info, View Customer |
| `service_reminders_queue` | Trigger on sales_master |
| `rto_queue` | RTO Queue page, rc_status_sms_queue |
| *(DMS fill row)* | **`form_dms.py`** (inline SQL) + future **`add_sales_staging.payload_json`**; `DMS_Form_Values.txt` under `ocr_output` |
| `form_vahan_view` | Vahan field inspection and `Vahan_Form_Values.txt` generation |
| `form_insurance_view` | Hero/MISP insurance fill and `Insurance_Form_Values.txt` generation |
| `rc_status_sms_queue` | RC status SMS sending |
| `bulk_loads` | Bulk ingest, queue publish/lease, dashboard, retry prep, action-taken tracking |
| `add_sales_staging` | Validated Add Sales JSON before master commit; **`staging_id`** for Create Invoice (**LLD §2.2a**); script **`DDL/alter/13a_add_sales_staging.sql`** |

---

## `add_sales_staging`

**Purpose:** Server-side snapshot for Add Sales **Create Invoice (DMS)**. On each successful **`POST /submit-info`**, the API inserts or updates a **draft** row (`payload_json`, `dealer_id`) only and returns **`staging_id`**. Masters upsert after successful **Create Invoice**; the row may then move to **`committed`** (**LLD §2.2a**). **Create Invoice** passes **`staging_id`** so DMS fill reads **`payload_json`**.

| Column | Type | Null | Default | Notes |
|--------|------|-----:|---------|-------|
| `staging_id` | `uuid` | NO |  | Primary key; new UUID on insert, or client may send existing **`staging_id`** on **`POST /submit-info`** to update that draft (same **`dealer_id`**) |
| `dealer_id` | `integer` | NO |  | FK → `dealer_ref.dealer_id` |
| `payload_json` | `jsonb` | NO |  | Merged payload (same logical shape as **`POST /submit-info`**); after DMS commit may include **`customer_id`** / **`vehicle_id`** |
| `status` | `varchar(32)` | NO | `'draft'` | `draft` / `committed` / `abandoned` |
| `created_at` | `timestamptz` | NO | `now()` | |
| `updated_at` | `timestamptz` | NO | `now()` | |
| `expires_at` | `timestamptz` | YES |  | Optional TTL for cleanup |

**Primary key:** `staging_id`

**Foreign key:** `dealer_id` → `dealer_ref(dealer_id)`

**Script:** `DDL/alter/13a_add_sales_staging.sql`

---

## Document Control

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | Mar 2025 | Initial Database DDL |
| 0.2 | Mar 2025 | vehicle_master: added model, colour, oem_name, vehicle_type, num_cylinders, horse_power, length_mm, fuel_type; sales_master: sales_id PK; RTO table schema (application_id PK, sales_id, rto_fees, pay_txn_id, etc.); service_reminders_queue: sales_id FK; added rc_status_sms_queue; insurance_master: insurance_year; Table Usage Summary |
| 0.7 | Mar 2026 | Renamed active RTO table to `rto_queue`; Add Sales now queues RTO work instead of auto-running dummy Vahan |
| 0.3 | Mar 2026 | Added `bulk_loads` hot schema, lifecycle columns, indexes, and operational notes |
| 0.4 | Mar 2026 | Added `form_vahan_view` for Vahan label inspection and runtime export support |
| 0.5 | Mar 2026 | Added `form_dms_view` for DMS label inspection and runtime export support |
| 0.6 | Mar 2026 | Added `vehicle_master.vehicle_price`; DMS/Vahan Playwright now read field values only from `form_dms_view` / `form_vahan_view` |
| 0.8 | Mar 2026 | Renamed `total_amount` / `total_cost` references to `vehicle_price` across schema, views, and UI |
| 0.9 | Mar 2026 | Added `customer_master.financier`, `customer_master.marital_status`, and `customer_master.nominee_gender` for details-sheet capture |
| 1.0 | Mar 2026 | Added `dealer_ref.rto_name` and seeded `RTO-Bharatpur` for dealer `100001` |
| 1.1 | Mar 2026 | Added `customer_master.alt_phone_num` for Alternate/Landline number and mapped it to DMS/Insurance automation usage |
| 1.2 | Mar 2026 | Added `customer_master.dms_relation_prefix`, `father_or_husband_name`, `dms_contact_path`; extended `form_dms_view`; documented `vehicle_price` as ex-showroom (Order Value) |
| 1.3 | Mar 2026 | Added `customer_master.care_of` (Aadhaar QR); DMS Father/Husband via `form_dms_view` uses `care_of` with legacy fallback to `father_or_husband_name`; Submit Info persists `care_of` |
| 1.4 | Mar 2026 | `vehicle_master.dms_sku`; `form_dms_view` includes **City** (`customer_master.city`); script `DDL/alter/10h_form_dms_view_city_vehicle_dms_sku.sql`; Fill DMS persists full Siebel scrape via `update_vehicle_master_from_dms` |
| 1.5 | Mar 2026 | `vehicle_master.vehicle_price` renamed to **`vehicle_ex_showroom_price`** (`DDL/alter/03j_vehicle_master_rename_vehicle_price_to_vehicle_ex_showroom_price.sql`); `form_vahan_view` column alias remains **`vehicle_price`**; `update_vehicle_master_from_dms` maps scrape **raw_key_num** into **`key_num`** when **key_num** absent |
| 1.6 | Mar 2026 | `sales_master.order_number`, **`invoice_number`** — DMS scrape persistence (`DDL/alter/05h_sales_master_add_order_invoice_numbers.sql`, **`update_sales_master_from_dms_scrape`** in `fill_hero_dms_service.py`) |
| 1.7 | Mar 2026 | `sales_master.enquiry_number` — DMS Enquiry# persistence (`DDL/alter/05i_sales_master_add_enquiry_number.sql`); `vehicle_ex_showroom_cost` now mapped to `vehicle_ex_showroom_price`; `update_sales_master_from_dms_scrape` called for real Siebel path |
| 1.8 | Mar 2026 | **`update_vehicle_master_from_dms`** no longer updates **`raw_frame_num`** / **`raw_engine_num`** (detail-sheet identity for `form_dms_view` partials / Add Enquiry search) |
| 1.9 | Mar 2026 | **`form_insurance_view`** (`DDL/alter/10j_form_insurance_view.sql`): stitches existing `customer_master` / `vehicle_master` / latest `insurance_master` per sale; Hero proposal uses hardcoded defaults for email/add-ons/payment/registration date |
| 2.0 | Mar 2026 | Add Sales **Generate Insurance** eligibility uses existing **`insurance_master.policy_num`** (non-empty) via `GET /add-sales/create-invoice-eligibility`; optional **`DDL/alter/12i_insurance_master_drop_insurance_automation_completed.sql`** removes **`insurance_automation_completed`** if it was added experimentally |
| 2.1 | Mar 2026 | **`customer_master.dms_contact_id`** (`DDL/alter/02k_customer_master_add_dms_contact_id.sql`) — optional Siebel Contact Id from DMS automation |
| 2.2 | Mar 2026 | **`add_sales_staging`** — Add Sales deferred commit staging (`DDL/alter/13a_add_sales_staging.sql`); **LLD §2.2a** |
| 2.3 | Mar 2026 | Dropped **`form_dms_view`** (`DDL/alter/13b_drop_form_dms_view.sql`); DMS fill via **`form_dms.py`** inline query + staging JSON |
| 2.4 | Mar 2026 | **`POST /submit-info`** inserts/updates **draft** **`add_sales_staging`** (`persist_staging_for_submit`); response **`staging_id`** |
| 2.5 | Mar 2026 | **`form_insurance_view`** / **BR-20**: operational notes — view + **`payload_json`** merge; **`fetch_staging_payload`** |
| 2.6 | Mar 2026 | **§9c**: **`form_insurance_view`** + **`payload_json`** as **joint** complete GI inputs (**BR-20**) |
| 2.7 | Mar 2026 | **`insurance_master.nominee_gender`**; dropped **`customer_master.nominee_gender`** and **`father_or_husband_name`**; **`form_dms_view`** / **`form_dms.py`**: relation prefix from address + gender fallback, Father/Husband from **`care_of` only** — **`DDL/alter/14a_nominee_gender_insurance_drop_customer_legacy.sql`** (and **`10j_form_insurance_view.sql`** add column + view) |
| 2.8 | Mar 2026 | **`vehicle_master.variant`**; **`place_of_registeration`** → `varchar(128)`; partial unique VIN index on **`chassis`**; dropped **`dms_sku`**; **`update_vehicle_master_from_dms`**: **`vehicle_type`** ALL CAPS; 2W derivations (motorcycle/scooter); RTO/OEM from dealer — **`DDL/alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql`** |
| 2.9 | Mar 2026 | **`sales_master`**: **`order_number`** / **`invoice_number`** / **`enquiry_number`** documented as scraped at **different DMS stages**; **`vahan_application_id`** / **`rto_charges`** documented as **Vahan/RTO** only — **BRD §6.1d** |
| 2.10 | Mar 2026 | **`sales_master`**: master commit **fails on duplicate** **`(customer_id, vehicle_id)`** — **`add_sales_commit_service`** plain `INSERT` + **`ValueError`** on **`uq_sales_customer_vehicle`** |
| 2.11 | Mar 2026 | **`insurance_master`**: app **INSERT** only for **`(customer_id, vehicle_id, insurance_year)`** (**`uq_insurance_customer_vehicle_year`**); post–**Issue Policy** scrape **UPDATE**s **`policy_num`** / **`insurance_cost`** (**`DDL/alter/14b_insurance_master_add_insurance_cost.sql`**) — **FR-18b** / **`add_sales_commit_service`** |
| 2.12 | Mar 2026 | Dropped **`vehicle_master.horse_power`** — not sourced from DMS (**`DDL/alter/15b_vehicle_master_drop_horse_power.sql`**); Form 20 field 19 remains in the template but is left blank |
| 2.13 | Mar 2026 | **No schema change.** Siebel **`prepare_vehicle`** navigation order (left Search Results drill-in, key/battery, inventory gate, Serial/Features/Pre-check/PDI) is automation-only — see **LLD** **6.72**, **BRD** **3.28** |
| 2.14 | Mar 2026 | **No schema change.** Siebel left-pane VIN jqGrid click hardening (**`gview_s_1001_l`** / **`ui-jqgrid-view`**) — **LLD** **6.73** |
| 2.15 | Mar 2026 | **No schema change.** Siebel single-hit **Title** VIN drilldown fallback when full chassis not yet in scrape — **LLD** **6.74** |
| 2.16 | Mar 2026 | **No schema change.** Siebel **Features** HHML visibility guard (skip redundant VIN/Serial grid drill) — **LLD** **6.75** |
| 2.17 | Mar 2026 | **No schema change.** Siebel **Features** step: **Features in Vehicles** landmark + **`#s_vctrl_div`** tab click — **LLD** **6.76**, **BRD** **3.31** |
| 2.18 | Mar 2026 | **No schema change.** Siebel **`prepare_vehicle`**: post–**Serial** drill, HHML scrape without Features tab click — **LLD** **6.77**, **BRD** **3.32** |
| 2.19 | Mar 2026 | **No schema change.** Siebel **Features** **`summary="Features"`** grid scrape — **LLD** **6.78**, **BRD** **3.33** |
| 2.20 | Mar 2026 | **No schema change.** HHML feature-value fallback now reads cell **`title`** and explicit ids after Serial drill before Pre-check/PDI — **LLD** **6.79**, **BRD** **3.34** |
| 2.21 | Mar 2026 | **No schema change.** Siebel **`[frame-focus]`** diagnostic logging (Serial → Features → Pre-check/PDI) — **LLD** **6.80**, **BRD** **3.35** |
| 2.22 | Mar 2026 | **No schema change.** Restored serial-detail vehicle-prep order from commit **`ab903064`** (Pre-check/PDI helper with feature-id scrape before Features tab scrape) — **LLD** **6.81**, **BRD** **3.36** |
| 2.23 | Mar 2026 | **No schema change.** **`cubic_capacity`** scrape stores numeric token only — **LLD** **6.82**, **BRD** **3.37** |
| 2.24 | Mar 2026 | **No schema change.** Payments flow: primary short tab activation and **Ctrl+S** save fallback with Transaction# verification — **LLD** **6.83**, **BRD** **3.38** |
| 2.25 | Mar 2026 | **No schema change.** Payments save action order updated to **Ctrl+S primary** with Save icon fallback; Transaction# remains mandatory success verification — **LLD** **6.84**, **BRD** **3.39** |
| 2.26 | Mar 2026 | **No schema change.** Siebel **`_siebel_diag_note`** + video path / **`_add_customer_payment`** diagnostic **`note`** lines (UTC inline + **`Playwright_DMS.txt`** line timestamps) — **LLD** **6.85**. **Superseded in part by 2.32** / **LLD** **6.91**. |
| 2.27 | Mar 2026 | **No schema change.** **LLD** **§2.4d.1** — trial field list + example JSON for Payment Lines root hint (future fast path); no DB table. |
| 2.28 | Mar 2026 | **No schema change.** **`Playwright_DMS.txt`** now appends automated **`payment_lines_root_hint`** JSON after Payment Lines gather — **LLD** **6.87**, **§2.4d.1**. **Superseded by 2.32** / **LLD** **6.91** (JSON append removed). |
| 2.29 | Mar 2026 | **No schema change.** Optional **`DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_*`** env fast path for Payment Lines frame — **LLD** **6.88**, **§2.4d.1**. |
| 2.30 | Mar 2026 | **No schema change.** Built-in **`_hero_default_payment_lines_root_hint`**; env hint optional override — **LLD** **6.89**. |
| 2.31 | Mar 2026 | **No schema change.** Temporary **`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`** gate (video SOP stops after payments) — **LLD** **6.90** (**superseded:** removed — **LLD** **6.91**). |
| 2.32 | Mar 2026 | **No schema change.** Siebel **`Playwright_DMS.txt`** logging cleanup (no trial JSON block; diag trim) — **LLD** **6.91**. |
| 2.33 | Mar 2026 | **No schema change.** Restored **`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`** after payments on video SOP — **LLD** **6.92**. |
| 2.34 | Mar 2026 | **No schema change.** Add Enquiry: skip second vehicle list scrape when **`prepare_vehicle`** merge present — **LLD** **6.93** (**superseded by** **2.35** / **LLD** **6.94**). |
| 2.35 | Mar 2026 | **No schema change.** Add Enquiry: skip **`_siebel_vehicle_find_chassis_engine_enter`** when merge ready — **LLD** **6.94** (**superseded by** **2.36** / **LLD** **6.95**). |
| 2.36 | Mar 2026 | **No schema change.** Add Enquiry: always **`_siebel_vehicle_find_chassis_engine_enter`**; **`reuse_vehicle_dict`** skips post-drill scrape only — **LLD** **6.95**. |
| 2.37 | Mar 2026 | **No schema change.** Contact Find strategies 1–2 (bounded waits + two-step Find) — **LLD** **6.96**. |
| 2.38 | Mar 2026 | **No schema change.** **`[TRACE:FC→FN]`** log lines — **LLD** **6.97**. |
| 2.39 | Mar 2026 | **No schema change.** Mobile Search Results iframe hint env — **LLD** **6.98**. |
| 2.40 | Mar 2026 | **No schema change.** Title drilldown / Contact_Enquiry subgrid trial **`note`** JSON — **LLD** **6.99** / **§2.4d.3**. |
