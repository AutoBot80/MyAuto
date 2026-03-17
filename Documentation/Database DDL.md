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
| `profession` | `varchar(16)` | YES |  | Customer profession (e.g. Service, Business) |
| `file_location` | `text` | YES |  | File location / sub-folder name where scans are placed |
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
| `key_num` | `varchar(32)` | YES |  | Key number |
| `engine` | `varchar(64)` | YES |  | Engine number |
| `chassis` | `varchar(64)` | YES |  | Chassis number |
| `battery` | `varchar(64)` | YES |  | Battery number |
| `plate_num` | `varchar(32)` | YES |  | Plate number |
| `model` | `varchar(64)` | YES |  | Vehicle model |
| `colour` | `varchar(64)` | YES |  | Vehicle colour |
| `raw_frame_num` | `varchar(32)` | YES |  | Raw extracted frame/chassis number |
| `raw_engine_num` | `varchar(32)` | YES |  | Raw extracted engine number |
| `raw_key_num` | `varchar(32)` | YES |  | Raw extracted key number |
| `year_of_mfg` | `integer` | YES |  | Year of manufacture (yyyy) |
| `cubic_capacity` | `numeric(10,2)` | YES |  | Cubic capacity (cc) |
| `body_type` | `varchar(16)` | YES |  | Body type (e.g. Sedan, SUV) |
| `seating_capacity` | `integer` | YES |  | Seating capacity |
| `place_of_registeration` | `varchar(32)` | YES |  | Place of registration |
| `oem_name` | `varchar(64)` | YES |  | OEM / Make (e.g. Hero MotoCorp); Form 20 field 16 |
| `vehicle_type` | `varchar(32)` | YES |  | Type of vehicle (e.g. LMV, 2W) |
| `num_cylinders` | `integer` | YES |  | Number of cylinders |
| `horse_power` | `numeric(10,2)` | YES |  | Horse power |
| `length_mm` | `integer` | YES |  | Length in mm |
| `fuel_type` | `varchar(16)` | YES |  | Fuel type (e.g. Petrol, Diesel) |

**Primary key:** `vehicle_master_pkey` on (`vehicle_id`)

**Unique:** `uq_vehicle_raw_triple` on (`raw_frame_num`, `raw_engine_num`, `raw_key_num`)

---

## 4) `sales_master`

**Purpose:** Sales master linking customer and vehicle. One row per (customer, vehicle). sales_id is PK; used by rto_payment_details and service_reminders_queue.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `sales_id` | `integer` | NO | `nextval('sales_master_sales_id_seq'::regclass)` | Primary key (auto-generated) |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `billing_date` | `timestamptz` | NO | `now()` | System date/time |
| `dealer_id` | `integer` | YES |  | FK → `dealer_ref(dealer_id)` |

**Primary key:** `sales_master_pkey` on (`sales_id`)

**Unique:** `uq_sales_customer_vehicle` on (`customer_id`, `vehicle_id`)

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
| `policy_broker` | `varchar(255)` | YES |  | Policy broker |
| `premium` | `numeric(12,2)` | YES |  | Premium amount |

**Primary key:** `insurance_master_pkey` on (`insurance_id`)

**Unique:** `uq_insurance_customer_vehicle_year` on (`customer_id`, `vehicle_id`, `insurance_year`)

**Foreign keys:**
- `fk_insurance_customer`: (`customer_id`) → `customer_master(customer_id)`
- `fk_insurance_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`

*Note: Alter 06b optionally changes FK to sales_master (customer_id, vehicle_id) for stricter referential integrity.*

---

## 8) `service_reminders_queue`

**Purpose:** Queue of service reminders per customer/vehicle. Populated by trigger fn_sales_master_sync_service_reminders when sales_master is upserted and dealer has auto_sms_reminders = Y.

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

## 9) `rto_payment_details`

**Purpose:** RTO registration applications; one row per application. Populated when Fill Forms completes the RTO (Vahan) step; status Pending until payment, then Paid with payment_date and pay_txn_id.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `application_id` | `varchar(128)` | NO |  | Primary key; Application ID from Vahan |
| `sales_id` | `integer` | NO |  | FK → `sales_master(sales_id)`; UNIQUE |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `dealer_id` | `integer` | YES |  | Dealer reference |
| `name` | `varchar(255)` | YES |  | Customer name (denormalized) |
| `mobile` | `varchar(16)` | YES |  | Customer mobile |
| `chassis_num` | `varchar(64)` | YES |  | Chassis number |
| `register_date` | `date` | NO | `CURRENT_DATE` | Date row added |
| `rto_fees` | `numeric(12,2)` | NO |  | RTO fees due (from Vahan) |
| `status` | `varchar(32)` | NO | `'Pending'` | e.g. Pending, Paid |
| `pay_txn_id` | `varchar(64)` | YES |  | Transaction ID when paid |
| `operator_id` | `varchar(64)` | YES |  | POS / operator identifier |
| `payment_date` | `date` | YES |  | Date paid (when status = Paid) |
| `rto_status` | `varchar(32)` | NO | `'Registered'` | RTO registration status |
| `subfolder` | `varchar(128)` | YES |  | Upload subfolder for this sale |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |

**Primary key:** `rto_payment_details_pkey` on (`application_id`)

**Unique:** `uq_rto_sales_id` on (`sales_id`), `uq_rto_customer_vehicle` on (`customer_id`, `vehicle_id`)

**Foreign keys:**
- `fk_rto_sales`: (`sales_id`) → `sales_master(sales_id)`

---

## 10) `rc_status_sms_queue`

**Purpose:** SMS queue for RC status notifications; populated when RTO payment is done.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('rc_status_sms_queue_id_seq'::regclass)` | Primary key |
| `sales_id` | `integer` | NO |  | FK → `sales_master(sales_id)` |
| `dealer_id` | `integer` | YES |  | Dealer; validated via sales_master (sales_id, dealer_id) |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `customer_mobile` | `varchar(16)` | YES |  | Customer mobile for SMS |
| `message_type` | `varchar(64)` | NO |  | Message type |
| `sms_status` | `varchar(32)` | NO | `'Pending'` | e.g. Pending, Sent |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |

**Primary key:** `rc_status_sms_queue_pkey` on (`id`)

**Foreign keys:**
- `fk_rc_sales`: (`sales_id`) → `sales_master(sales_id)`
- `fk_rc_sales_dealer`: (`sales_id`, `dealer_id`) → `sales_master(sales_id`, `dealer_id)`
- `fk_rc_rto`: (`customer_id`, `vehicle_id`) → `rto_payment_details(customer_id, vehicle_id)`

---

## Table Usage Summary

| Table | Used by |
|-------|---------|
| `ai_reader_queue` | OCR service, uploads router |
| `customer_master` | Submit Info, customer search, Form 20, RTO |
| `vehicle_master` | Submit Info, Form 20, Fill DMS (update), RTO |
| `sales_master` | Submit Info, RTO, service reminders, insurance |
| `oem_ref` | dealer_ref, Form 20 (oem_name via dealer) |
| `oem_service_schedule` | Trigger for service_reminders_queue |
| `dealer_ref` | Form 20, sales_master, service reminders |
| `insurance_master` | Submit Info, View Customer |
| `service_reminders_queue` | Trigger on sales_master |
| `rto_payment_details` | RTO Payments Pending page, rc_status_sms_queue |
| `rc_status_sms_queue` | RC status SMS sending |

---

## Document Control

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | Mar 2025 | Initial Database DDL |
| 0.2 | Mar 2025 | vehicle_master: added model, colour, oem_name, vehicle_type, num_cylinders, horse_power, length_mm, fuel_type; sales_master: sales_id PK; rto_payment_details: updated schema (application_id PK, sales_id, rto_fees, pay_txn_id, etc.); service_reminders_queue: sales_id FK; added rc_status_sms_queue; insurance_master: insurance_year; Table Usage Summary |
