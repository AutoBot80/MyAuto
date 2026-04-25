-- Dealer reference. Run after oem_ref. Replaces dealer_master.
-- Run against database: auto_ai
-- Logical column order: dealer_id, dealer_name, subdealer_type, address, pin, city, state,
--   parent_id, phone, oem_id, auto_sms_reminders, rto_name, prefer_insurer, hero_cpi

CREATE TABLE IF NOT EXISTS dealer_ref (
    dealer_id SERIAL PRIMARY KEY,
    dealer_name VARCHAR(255) NOT NULL,
    subdealer_type VARCHAR(64),
    address TEXT,
    pin CHAR(6),
    city TEXT,
    state TEXT,
    parent_id INTEGER,
    phone VARCHAR(16),
    oem_id INTEGER,
    auto_sms_reminders CHAR(1),
    rto_name VARCHAR(128),
    prefer_insurer VARCHAR(255),
    hero_cpi CHAR(1) NOT NULL DEFAULT 'N',
    CONSTRAINT fk_dealer_ref_oem FOREIGN KEY (oem_id) REFERENCES oem_ref(oem_id),
    CONSTRAINT chk_dealer_ref_auto_sms_reminders CHECK (auto_sms_reminders IN ('Y', 'N')),
    CONSTRAINT chk_dealer_ref_hero_cpi CHECK (hero_cpi IN ('Y', 'N'))
);

COMMENT ON TABLE dealer_ref IS 'Dealer reference; parent_id for hierarchy';
COMMENT ON COLUMN dealer_ref.subdealer_type IS
  'Subdealer category/line (e.g. ARD, AD) aligned with discount rules; optional for parent/umbrella dealers';
COMMENT ON COLUMN dealer_ref.oem_id IS 'FK to oem_ref (OEM/brand); supplied on insert, not auto-generated';
COMMENT ON COLUMN dealer_ref.rto_name IS 'Dealer-mapped RTO office name (e.g. RTO-Bharatpur)';
COMMENT ON COLUMN dealer_ref.auto_sms_reminders IS 'Y or N; when Y, trigger adds rows to service_reminders_queue on sales_master upsert';
COMMENT ON COLUMN dealer_ref.prefer_insurer IS 'Optional canonical MISP insurer label; when set, merged details-sheet insurer fuzzy-matches to this (>=20%) it replaces insurer for KYC — see insurance_form_values.build_insurance_fill_values';
COMMENT ON COLUMN dealer_ref.hero_cpi IS 'Y or N: MISP proposal CPA Hero CPI add-on row (label varies — NIC/CPI); Y check, N uncheck — DDL/alter/17a_dealer_ref_hero_cpi_form_insurance_view.sql';
