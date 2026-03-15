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
| `phone` | `varchar(16)` | YES |  | Phone number |
| `mobile_number` | `integer` | YES |  | Customer mobile number (10 digits) |
| `profession` | `varchar(16)` | YES |  | Customer profession (e.g. Service, Business) |
| `file_location` | `text` | YES |  | File location / sub-folder name where scans are placed |
| `gender` | `varchar(8)` | YES |  | Gender from Aadhar QR (e.g. M, F) |
| `date_of_birth` | `varchar(20)` | YES |  | Date of birth (dd/mm/yyyy); default date format for app and DB |

**Primary key:** `customer_master_pkey` on (`customer_id`)

**Unique:** `uq_customer_aadhar_phone` on (`aadhar`, `phone`) — customer identified by last 4 Aadhar + phone

---

## 3) `vehicle_master`

**Purpose:** Vehicle master data.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `vehicle_id` | `integer` | NO | `nextval('vehicle_master_vehicle_id_seq'::regclass)` | Primary key |
| `key_num` | `varchar(32)` | YES |  | Key number |
| `engine` | `varchar(64)` | YES |  | Engine number |
| `chassis` | `varchar(64)` | YES |  | Chassis number |
| `battery` | `varchar(64)` | YES |  | Battery number |
| `plate_num` | `varchar(32)` | YES |  | Plate number |
| `raw_frame_num` | `varchar(32)` | YES |  | Raw extracted frame/chassis number |
| `raw_engine_num` | `varchar(32)` | YES |  | Raw extracted engine number |
| `raw_key_num` | `varchar(32)` | YES |  | Raw extracted key number |
| `year_of_mfg` | `integer` | YES |  | Year of manufacture (yyyy) |
| `cubic_capacity` | `numeric(10,2)` | YES |  | Cubic capacity (cc) |
| `body_type` | `varchar(16)` | YES |  | Body type (e.g. Sedan, SUV) |
| `seating_capacity` | `integer` | YES |  | Seating capacity |
| `place_of_registeration` | `varchar(32)` | YES |  | Place of registration |

**Primary key:** `vehicle_master_pkey` on (`vehicle_id`)

---

## 4) `sales_master`

**Purpose:** Sales master linking customer and vehicle.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `billing_date` | `timestamptz` | NO | `now()` | System date/time |
| `dealer_id` | `integer` | YES |  | FK → `dealer_ref(dealer_id)` |

**Primary key:** `sales_master_pkey` on (`customer_id`, `vehicle_id`)

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

**Purpose:** OEM service schedule (service number, type, days from billing, active flag).

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

**Purpose:** Dealer reference; replaces dealer_master.

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

**Purpose:** Insurance policy records linked to customer and vehicle.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `insurance_id` | `integer` | NO | `nextval('insurance_master_insurance_id_seq'::regclass)` | Primary key (auto-generated) |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `year` | `integer` | YES |  | Policy year (yyyy) |
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

**Foreign keys:**
- `fk_insurance_customer`: (`customer_id`) → `customer_master(customer_id)`
- `fk_insurance_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`

---

## 8) `service_reminders_queue`

**Purpose:** Queue of service reminders per customer/vehicle.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('service_reminders_queue_id_seq'::regclass)` | Primary key (auto-generated) |
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
- `fk_service_reminders_customer`: (`customer_id`) → `customer_master(customer_id)`
- `fk_service_reminders_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`
- `fk_service_reminders_dealer`: (`dealer_id`) → `dealer_ref(dealer_id)`

**Check:** `chk_service_reminders_service_type` — `service_type` IN ('Free', 'Paid')

---

## 9) `rto_payment_details`

**Purpose:** RTO registration applications; one row per application. Populated when Fill Forms completes the RTO (Vahan) step; status Pending until payment, then Paid with payment_date and txn_id.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `id` | `integer` | NO | `nextval('rto_payment_details_id_seq'::regclass)` | Primary key (auto-generated) |
| `customer_id` | `integer` | NO |  | FK → `customer_master(customer_id)` |
| `name` | `varchar(255)` | YES |  | Customer name (denormalized) |
| `mobile` | `varchar(16)` | YES |  | Customer mobile |
| `chassis_num` | `varchar(64)` | YES |  | Chassis number |
| `application_num` | `varchar(128)` | NO |  | Application ID from Vahan |
| `submission_date` | `date` | NO | `CURRENT_DATE` | Date row added (dd-mm-yyyy in app) |
| `rto_payment_due` | `numeric(12,2)` | NO |  | RTO fees due (from Vahan) |
| `status` | `varchar(32)` | NO | `'Pending'` | e.g. Pending, Paid |
| `pos_mgr_id` | `varchar(64)` | YES |  | POS / manager identifier |
| `txn_id` | `varchar(64)` | YES |  | Transaction ID when paid |
| `payment_date` | `date` | YES |  | Date paid (when status = Paid) |
| `created_at` | `timestamptz` | NO | `now()` | Created timestamp |

**Primary key:** `rto_payment_details_pkey` on (`id`)

**Foreign keys:**
- `rto_payment_details_customer_id_fkey`: (`customer_id`) → `customer_master(customer_id)`

