-- Dealer reference. Run after oem_ref. Replaces dealer_master.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS dealer_ref (
    dealer_id SERIAL PRIMARY KEY,
    dealer_name VARCHAR(255) NOT NULL,
    oem_id INTEGER,
    address TEXT,
    pin CHAR(6),
    city TEXT,
    state TEXT,
    parent_id INTEGER,
    phone VARCHAR(16),
    auto_sms_reminders CHAR(1),
    CONSTRAINT fk_dealer_ref_oem FOREIGN KEY (oem_id) REFERENCES oem_ref(oem_id),
    CONSTRAINT chk_dealer_ref_auto_sms_reminders CHECK (auto_sms_reminders IN ('Y', 'N'))
);

COMMENT ON TABLE dealer_ref IS 'Dealer reference; parent_id for hierarchy';
COMMENT ON COLUMN dealer_ref.oem_id IS 'FK to oem_ref (OEM/brand); supplied on insert, not auto-generated';
COMMENT ON COLUMN dealer_ref.auto_sms_reminders IS 'Y or N; when Y, trigger adds rows to service_reminders_queue on sales_master upsert';
