-- OEM service schedule: service number, type, days from billing per OEM.
-- Run after oem_ref. Run against database: auto_ai

CREATE TABLE IF NOT EXISTS oem_service_schedule (
    oem_id INTEGER NOT NULL,
    service_num INTEGER,
    service_type VARCHAR(16),
    days_from_billing INTEGER,
    active_flag CHAR(1),
    reminder_type VARCHAR(16),
    CONSTRAINT fk_oem_service_schedule_oem FOREIGN KEY (oem_id) REFERENCES oem_ref(oem_id),
    CONSTRAINT chk_oem_service_schedule_service_type CHECK (service_type IN ('Free', 'Paid')),
    CONSTRAINT chk_oem_service_schedule_active_flag CHECK (active_flag IN ('Y', 'N'))
);

COMMENT ON TABLE oem_service_schedule IS 'OEM service schedule: service number, type, days from billing, active Y/N';
COMMENT ON COLUMN oem_service_schedule.service_num IS 'Service sequence number';
COMMENT ON COLUMN oem_service_schedule.service_type IS 'Free or Paid';
COMMENT ON COLUMN oem_service_schedule.days_from_billing IS 'Days from billing date for this service';
COMMENT ON COLUMN oem_service_schedule.active_flag IS 'Y or N';
COMMENT ON COLUMN oem_service_schedule.reminder_type IS 'e.g. SMS, Email';
