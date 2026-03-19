-- Rename legacy total_amount column to vehicle_price for current automation/UI naming.
-- Run against database: auto_ai

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'vehicle_master'
          AND column_name = 'total_amount'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'vehicle_master'
          AND column_name = 'vehicle_price'
    ) THEN
        ALTER TABLE vehicle_master RENAME COLUMN total_amount TO vehicle_price;
    END IF;
END $$;

COMMENT ON COLUMN vehicle_master.vehicle_price IS 'Latest DMS vehicle price used as Vahan vehicle_price source';
