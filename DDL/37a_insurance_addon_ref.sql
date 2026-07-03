-- MISP proposal add-on preset combinations, scoped per portal insurer.
-- Run after: DDL/28_master_ref.sql (portal INSURER rows with comments = 'Y')
-- Run against database: auto_ai

BEGIN;

CREATE TABLE IF NOT EXISTS insurance_addon_ref (
  insurance_addon_id SERIAL PRIMARY KEY,
  insurer            VARCHAR(255) NOT NULL,
  display_label      VARCHAR(255) NOT NULL,
  nd_cover           CHAR(1) NOT NULL DEFAULT 'N',
  rti                CHAR(1) NOT NULL DEFAULT 'N',
  rim_safeguard      CHAR(1) NOT NULL DEFAULT 'N',
  rsa                CHAR(1) NOT NULL DEFAULT 'N',
  sort_order         INTEGER NOT NULL DEFAULT 0,
  active_flag        CHAR(1) NOT NULL DEFAULT 'Y',
  insurer_ref_type   VARCHAR(32) GENERATED ALWAYS AS ('INSURER') STORED,
  CONSTRAINT chk_insurance_addon_ref_nd_cover CHECK (nd_cover IN ('Y', 'N')),
  CONSTRAINT chk_insurance_addon_ref_rti CHECK (rti IN ('Y', 'N')),
  CONSTRAINT chk_insurance_addon_ref_rim_safeguard CHECK (rim_safeguard IN ('Y', 'N')),
  CONSTRAINT chk_insurance_addon_ref_rsa CHECK (rsa IN ('Y', 'N')),
  CONSTRAINT chk_insurance_addon_ref_active_flag CHECK (active_flag IN ('Y', 'N')),
  CONSTRAINT uq_insurance_addon_ref_insurer_label UNIQUE (insurer, display_label),
  CONSTRAINT fk_insurance_addon_ref_insurer
    FOREIGN KEY (insurer_ref_type, insurer)
    REFERENCES master_ref (ref_type, ref_value)
);

CREATE INDEX IF NOT EXISTS idx_insurance_addon_ref_insurer
  ON insurance_addon_ref (insurer);

COMMENT ON TABLE insurance_addon_ref IS
  'Named MISP add-on checkbox presets per portal insurer; dealer_ref.insurance_addon FK';

COMMENT ON COLUMN insurance_addon_ref.insurer IS
  'Portal insurer label — same as dealer_ref.prefer_insurer / master_ref INSURER ref_value';

COMMENT ON COLUMN insurance_addon_ref.display_label IS
  'UI dropdown label, e.g. ND Cover, Rim Safeguard, RSA';

COMMENT ON COLUMN insurance_addon_ref.nd_cover IS
  'Y/N: nil-depreciation add-on (ND Cover on most insurers; ND Plus on NIC branch in Playwright)';

COMMIT;
