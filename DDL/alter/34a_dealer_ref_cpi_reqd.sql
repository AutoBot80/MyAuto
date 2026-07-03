-- Dealer-level CPI participation flag (separate from hero_cpi MISP checkbox automation).
-- Run against database: auto_ai

BEGIN;

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS cpi_reqd CHAR(1);

UPDATE dealer_ref
SET cpi_reqd = 'N'
WHERE cpi_reqd IS NULL;

UPDATE dealer_ref
SET cpi_reqd = 'Y'
WHERE dealer_name = 'Arya Agencies';

ALTER TABLE dealer_ref
  ALTER COLUMN cpi_reqd SET DEFAULT 'N';

ALTER TABLE dealer_ref
  ALTER COLUMN cpi_reqd SET NOT NULL;

ALTER TABLE dealer_ref
  DROP CONSTRAINT IF EXISTS chk_dealer_ref_cpi_reqd;

ALTER TABLE dealer_ref
  ADD CONSTRAINT chk_dealer_ref_cpi_reqd CHECK (cpi_reqd IN ('Y', 'N'));

COMMENT ON COLUMN dealer_ref.cpi_reqd IS
  'Y or N: whether dealer requires/participates in CPI; N for most dealers';

COMMIT;
