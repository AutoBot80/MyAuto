-- Generic reference rows keyed by type (e.g. INSURER). No FK dependencies.
-- Run against database: auto_ai
-- Primary key is (ref_type, ref_value) — no surrogate id column.

CREATE TABLE IF NOT EXISTS master_ref (
    ref_type VARCHAR(64) NOT NULL,
    ref_value VARCHAR(512) NOT NULL,
    comments TEXT,
    PRIMARY KEY (ref_type, ref_value)
);

COMMENT ON TABLE master_ref IS 'Typed reference values (e.g. INSURER canonical names)';
COMMENT ON COLUMN master_ref.ref_type IS 'Category key, e.g. INSURER, CPA';
COMMENT ON COLUMN master_ref.ref_value IS 'Display/canonical value for that type';
COMMENT ON COLUMN master_ref.comments IS
    'Optional notes. For ref_type CPA, stores the portal login URL (https://…). Other ref_types: optional notes or NULL.';
