# Challan master staging + challan_details_staging (locked decisions)

## Status

Build mode: **`challan_staging` has no production data**; **no downtime constraints**. Prefer clean DDL over compatibility shims.

---

## Locked decisions (product owner)

### 1) Naming and data

- Rename / replace line table as **`challan_details_staging`** (not `challan_staging`).
- Add **`challan_master_staging`** for header + batch lifecycle.
- **Keep `challan_batch_id` (UUID)** as the batch identifier (join key master ↔ details).

### 2) `num_vehicles`

- Set to **count of vehicle/detail rows** at the moment **Create Challan** is pressed (insert time).

### 3) `num_vehicles_prepared`

- Count vehicles for which **VIN and engine number were scraped** and **`prepare_vehicle` completed without errors** (aligns with treating those lines as successfully prepared; implementation maps to current **Ready** path + scrape fields as in code today).

### 4) `invoice_complete`

- Set **`true`** when **`invoice_number` is successfully scraped** from the DMS flow (same scrape surface as today’s order/invoice phase output).

### 5) Processed tab UX

- **Master rows only** for the main list.
- **Expand / sub-list:** show **failed vehicles** under a master row with **chassis/engine (or identifiers)** and **`last_error`** per line.
- **Partial batch state:** show **“x vehicles ready out of y total”**.
- **`invoice_status` (master):** **Pending** | **Failed** | **Completed**. Use **Completed** when invoice is successfully scraped (`invoice_complete` true). Use **Pending** whenever the invoice is not yet successfully scraped — including when **all vehicles are prepared but create order / invoice has not completed** (no separate “ready for order” label).

### 6) Retry / partial success (orchestration)

- **Retry** should **only re-run `prepare_vehicle` for failed vehicles**, then run the **create order** piece for the batch.
- If **`create_order` fails mid-flight**, **retry may run order/invoice completion without re-running `prepare_vehicle`** for lines that are already prepared.

---

## Architecture notes (implementation-facing)

- Split orchestration: **prepare phase** (per-line, failed → retry) vs **order/invoice phase** (batch, retryable without prepare when applicable).
- Master row holds: `from_dealer_id`, `to_dealer_id`, `challan_book_num`, `challan_date`, `num_vehicles`, `num_vehicles_prepared`, `invoice_complete`, plus **`invoice_status`** (**Pending** | **Failed** | **Completed**).
- Detail rows in **`challan_details_staging`**: per-line status, errors, `inventory_line_id`, raw engine/chassis, etc., linked by **`challan_batch_id`**.

---

## Implementation constraints

- **Siebel / contact sweep:** Do not change **duplicate-mobile / enquiry** sweep behavior in unrelated modules; subdealer challan uses **`hero_dms_playwright_customer_challan`** — orchestration refactors must not alter fragile Siebel paths unless explicitly scoped.

---

## Non-goals (unless added later)

- Incremental invoice for a **subset** of vehicles in separate orders (current automation assumes **one order** with **all** prepared lines).
- API versioning: build mode → **breaking API changes acceptable** if client is updated in same change set.

---

## Implementation todos (when executing)

1. DDL: `challan_master_staging`, `challan_details_staging`; FK by `challan_batch_id`; drop/rename old `challan_staging` if unused.
2. Repositories + `create_challan_staging_batch` / `run_subdealer_challan_batch` refactor + master aggregate updates.
3. Endpoints: list masters for Processed; retry prepare vs retry order; client Processed + expand failed list.
4. Update **Database DDL.md** + changelog; BRD/HLD/LLD only if you request doc sync.
