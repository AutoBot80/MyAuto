# DDL Scripts (PostgreSQL)

All Postgres DDL for the **auto_ai** database. Run in order when creating a fresh schema.

## Order

1. `01_ai_reader_queue.sql` — no dependencies  
2. `02_customer_master.sql`  
3. `03_vehicle_master.sql`  
4. `04_dealer_master.sql`  
5. `05_sales_master.sql` — requires customer_master, vehicle_master, dealer_master  

## Run (examples)

```bash
# Using psql (with pgpass or -W for password)
psql -h localhost -U postgres -d auto_ai -f DDL/01_ai_reader_queue.sql
psql -h localhost -U postgres -d auto_ai -f DDL/02_customer_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/03_vehicle_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/04_dealer_master.sql
psql -h localhost -U postgres -d auto_ai -f DDL/05_sales_master.sql
```

Or run all in order (Unix):

```bash
for f in DDL/0*.sql; do psql -h localhost -U postgres -d auto_ai -f "$f"; done
```

## Alter / migrations

One-off changes (e.g. new columns) go in **`DDL/alter/`** (e.g. `02a_customer_master_add_file_location.sql`). Run these against an existing database as needed.

## Maintenance

- Keep this folder in sync with **Documentation/Database DDL.md**.
- When adding/removing/altering tables: add or update the corresponding `NN_name.sql` script (and any `alter/` script) and the doc.
