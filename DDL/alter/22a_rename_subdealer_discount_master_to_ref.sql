-- Renames discount rules table to subdealer_discount_master_ref (*_ref naming).
-- Run once on databases that still have subdealer_discount_master.

ALTER TABLE subdealer_discount_master RENAME TO subdealer_discount_master_ref;

ALTER SEQUENCE subdealer_discount_master_subdealer_discount_id_seq
  RENAME TO subdealer_discount_master_ref_subdealer_discount_id_seq;
