# Database DDL
## auto_ai (PostgreSQL)

This document lists the current database tables and their columns. **Executable DDL scripts** are in the **`DDL/`** folder (e.g. `DDL/01_ai_reader_queue.sql`). Keep both this doc and the `DDL/` scripts updated when adding, removing, or altering tables.

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
| `aadhar` | `char(12)` | NO |  | Primary key (12 digits) |
| `name` | `text` | NO |  | Customer name |
| `address` | `text` | YES |  | Address |
| `pin` | `char(6)` | YES |  | PIN code |
| `city` | `text` | YES |  | City |
| `state` | `text` | YES |  | State |
| `phone` | `varchar(16)` | YES |  | Phone number |
| `file_location` | `text` | YES |  | File location / sub-folder name where scans are placed |

**Primary key:** `customer_master_pkey` on (`aadhar`)

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

**Primary key:** `vehicle_master_pkey` on (`vehicle_id`)

---

## 4) `sales_master`

**Purpose:** Sales master linking customer and vehicle.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `aadhar` | `char(12)` | NO |  | FK → `customer_master(aadhar)` |
| `vehicle_id` | `integer` | NO |  | FK → `vehicle_master(vehicle_id)` |
| `billing_date` | `timestamptz` | NO | `now()` | System date/time |
| `dealer_id` | `integer` | YES |  | FK → `dealer_master(dealer_id)` |

**Primary key:** `sales_master_pkey` on (`aadhar`, `vehicle_id`)

**Foreign keys:**
- `fk_sales_customer`: (`aadhar`) → `customer_master(aadhar)`
- `fk_sales_dealer`: (`dealer_id`) → `dealer_master(dealer_id)`
- `fk_sales_vehicle`: (`vehicle_id`) → `vehicle_master(vehicle_id)`

---

## 5) `dealer_master`

**Purpose:** Dealer master data.

| Column | Type | Null | Default | Notes |
|---|---|---:|---|---|
| `dealer_id` | `integer` | NO | `nextval('dealer_master_dealer_id_seq'::regclass)` | Primary key |
| `dealer_name` | `varchar(255)` | NO |  | Dealer name |
| `address` | `text` | YES |  | Address |
| `pin` | `char(6)` | YES |  | PIN code |
| `city` | `text` | YES |  | City |
| `state` | `text` | YES |  | State |
| `parent_id` | `integer` | YES |  | Parent dealer id (hierarchy) |
| `phone` | `varchar(16)` | YES |  | Phone (up to 16 digits) |

**Primary key:** `dealer_master_pkey` on (`dealer_id`)

