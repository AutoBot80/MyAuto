-- Service reminders queue: per customer/vehicle, service dates and reminder status.
-- Run after customer_master, vehicle_master. Run against database: auto_ai

CREATE TABLE IF NOT EXISTS service_reminders_queue (
    id SERIAL PRIMARY KEY,
    sales_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    billing_date DATE,
    service_date DATE,
    service_type VARCHAR(16),
    reminder_num INTEGER,
    reminder_date DATE,
    reminder_type VARCHAR(16),
    reminder_status VARCHAR(16),
    dealer_id INTEGER,
    CONSTRAINT fk_service_reminders_sales FOREIGN KEY (sales_id) REFERENCES sales_master(sales_id),
    CONSTRAINT chk_service_reminders_service_type CHECK (service_type IN ('Free', 'Paid'))
);

COMMENT ON TABLE service_reminders_queue IS 'Queue of service reminders per customer/vehicle';
COMMENT ON COLUMN service_reminders_queue.billing_date IS 'Billing/sale date';
COMMENT ON COLUMN service_reminders_queue.service_date IS 'Scheduled service date';
COMMENT ON COLUMN service_reminders_queue.service_type IS 'Free or Paid';
COMMENT ON COLUMN service_reminders_queue.reminder_num IS 'Trigger inserts one row per schedule with reminder_num 1';
COMMENT ON COLUMN service_reminders_queue.reminder_date IS 'Date when this reminder should be sent';
COMMENT ON COLUMN service_reminders_queue.reminder_type IS 'From oem_service_schedule (e.g. SMS)';
COMMENT ON COLUMN service_reminders_queue.reminder_status IS 'Status of reminder (e.g. pending, sent)';
COMMENT ON COLUMN service_reminders_queue.dealer_id IS 'FK to dealer_ref';
