-- vehicle_master: enforce uniqueness on (engine, chassis) when both are present.
-- Engine/chassis can be NULL; this partial unique index only applies when both are non-null and non-empty.

CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicle_engine_chassis
ON vehicle_master (engine, chassis)
WHERE engine IS NOT NULL AND engine <> ''
  AND chassis IS NOT NULL AND chassis <> '';

