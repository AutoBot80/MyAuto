-- Rename vehicle_master.vehicle_price → vehicle_ex_showroom_price (ex-showroom / Order Value from DMS).
-- Run after DDL/alter/03f_vehicle_master_add_total_amount.sql (or any script that created vehicle_price).
-- View must be dropped first (it references vehicle_price). Recreate with DDL/alter/10e_form_vahan_view.sql after this script.

DROP VIEW IF EXISTS form_vahan_view;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'vehicle_master'
          AND column_name = 'vehicle_price'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'vehicle_master'
          AND column_name = 'vehicle_ex_showroom_price'
    ) THEN
        ALTER TABLE vehicle_master RENAME COLUMN vehicle_price TO vehicle_ex_showroom_price;
    END IF;
END $$;

COMMENT ON COLUMN vehicle_master.vehicle_ex_showroom_price IS
    'Ex-showroom / Order Value from DMS; exposed as vehicle_price in form_vahan_view for Vahan automation';
