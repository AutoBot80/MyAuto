-- dealer_ref: add subdealer_type (VARCHAR(64), nullable). Safe to re-run.
-- For full logical column order, see greenfield **DDL/04b_dealer_ref.sql**; existing
-- DBs may keep older physical column order (PostgreSQL does not support reordering in place).
-- Run after dealer_ref exists. Run against database: auto_ai

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS subdealer_type VARCHAR(64);

COMMENT ON COLUMN dealer_ref.subdealer_type IS
  'Subdealer category/line (e.g. ARD, AD) aligned with discount rules; optional for parent/umbrella dealers';
