-- Hero Connect Siebel portal per dealer: ASC (edealerasc subdealers) vs HMCL (default main dealers).
-- Run against database: auto_ai

ALTER TABLE dealer_ref
  ADD COLUMN IF NOT EXISTS dms_siebel_portal VARCHAR(8);

ALTER TABLE dealer_ref
  DROP CONSTRAINT IF EXISTS chk_dealer_ref_dms_siebel_portal;

ALTER TABLE dealer_ref
  ADD CONSTRAINT chk_dealer_ref_dms_siebel_portal
  CHECK (dms_siebel_portal IS NULL OR dms_siebel_portal IN ('ASC', 'HMCL'));

COMMENT ON COLUMN dealer_ref.dms_siebel_portal IS
  'Hero Connect Siebel portal: ASC = edealerasc (subdealers); HMCL or NULL = edealerHMCL (main dealers).';

-- Known ASC subdealer (Siebel user must use /edealerasc_enu).
UPDATE dealer_ref SET dms_siebel_portal = 'ASC' WHERE dealer_id = 100003;
