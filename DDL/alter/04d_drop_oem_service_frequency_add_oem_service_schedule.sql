-- Drop oem_service_frequency; create oem_service_schedule.
-- Run against database: auto_ai

DROP TABLE IF EXISTS oem_service_frequency;

CREATE TABLE IF NOT EXISTS oem_service_schedule (
    oem_id INTEGER NOT NULL,
    service_num INTEGER,
    service_type VARCHAR(16),
    days_from_billing INTEGER,
    active_flag CHAR(1),
    CONSTRAINT fk_oem_service_schedule_oem FOREIGN KEY (oem_id) REFERENCES oem_ref(oem_id),
    CONSTRAINT chk_oem_service_schedule_service_type CHECK (service_type IN ('Free', 'Paid')),
    CONSTRAINT chk_oem_service_schedule_active_flag CHECK (active_flag IN ('Y', 'N'))
);

COMMENT ON TABLE oem_service_schedule IS 'OEM service schedule: service number, type, days from billing, active Y/N';
COMMENT ON COLUMN oem_service_schedule.service_num IS 'Service sequence number';
COMMENT ON COLUMN oem_service_schedule.service_type IS 'Free or Paid';
COMMENT ON COLUMN oem_service_schedule.days_from_billing IS 'Days from billing date for this service';
COMMENT ON COLUMN oem_service_schedule.active_flag IS 'Y or N';
