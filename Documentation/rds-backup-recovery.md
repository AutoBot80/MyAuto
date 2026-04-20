# RDS backup and recovery (Saathi)

This matches the PostgreSQL instance created in `terraform/network/rds.tf`.

## What is enabled today

- **Automated backups** with retention controlled by Terraform variable **`rds_backup_retention_period`** (this stack uses **15 days** in `terraform.tfvars`). Adjust and `terraform apply` if you need up to **35 days** of retention.
- **Storage encryption** (`storage_encrypted = true`).
- **Copy tags to snapshots** (`copy_tags_to_snapshot = true`) so manual snapshots stay traceable.
- **Deletion protection** is **on** for this stack (`rds_deletion_protection = true` in `terraform.tfvars`) so the instance cannot be deleted until protection is disabled.

The RDS master password is **managed in Secrets Manager** (`manage_master_user_password = true`); rotation is an AWS concern separate from application `DATABASE_URL` (refresh with `deploy/ec2/write-database-url.sh` when the secret changes).

## Where to see backups

1. AWS Console → **RDS** → **Databases** → select the instance (identifier from `terraform output -raw rds_identifier`).
2. **Maintenance & backups** tab: backup window, retention, latest restorable time (point-in-time).
3. **Snapshots**: automated recovery points and any **manual** snapshots you create before risky changes.

## Point-in-time restore (overview)

Use when you need to recover to a time within the retention window (for example, bad migration or accidental data loss).

1. RDS → **Automated backups** / instance → **Restore to point in time** (or create a **new** DB instance from a snapshot—same idea).
2. Choose a **new** identifier and subnet group/VPC consistent with your app (same VPC as Terraform is simplest).
3. Security group: attach the same RDS SG or clone rules so only the app tier can reach PostgreSQL.
4. After the new instance is **available**, point the app at the new endpoint (update `DATABASE_URL` / secret-driven URL), run migrations if needed, and validate.
5. **Cutover**: restart `saathi-api`, smoke-test `/health` and login. Decommission the old instance only after you are sure.

**Downtime**: plan for a controlled maintenance window unless you use a blue/green style (new endpoint, DNS or config switch).

## Manual snapshot before risky work

Before major schema changes or one-off bulk deletes:

- RDS → instance → **Actions** → **Take snapshot** — name it with date and ticket (e.g. `saathi-pre-migration-2026-04-17`).

## What to rehearse

Once per quarter (or before go-live), run a **dry run**: restore to a **temporary** instance in the same VPC, verify connectivity from a bastion or test SG, then delete the temp instance. This validates IAM, networking, and your runbook—not just AWS defaults.

## Related Terraform outputs

- `rds_identifier` — instance ID for console and alarms.
- `rds_endpoint` — host:port for the live database.
- `rds_master_user_secret_arn` — master credentials in Secrets Manager.