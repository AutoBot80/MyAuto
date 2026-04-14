-- Seed financiers for ref_type FINANCER. Run after DDL/28_master_ref.sql (table master_ref exists).
-- PowerShell: psql -U postgres -d auto_ai -f "DDL\seed_master_ref_financers.sql"

INSERT INTO master_ref (ref_type, ref_value) VALUES
('FINANCER', 'HDFC Bank'),
('FINANCER', 'ICICI Bank'),
('FINANCER', 'Axis Bank'),
('FINANCER', 'State Bank of India'),
('FINANCER', 'IDFC First Bank'),
('FINANCER', 'UCO Bank'),
('FINANCER', 'Indian Bank'),
('FINANCER', 'Central Bank of India'),
('FINANCER', 'Bank of India'),
('FINANCER', 'Jammu & Kashmir Bank'),
('FINANCER', 'Bajaj Auto Finance'),
('FINANCER', 'Bajaj Finance'),
('FINANCER', 'TVS Credit Services'),
('FINANCER', 'Hero Fincorp'),
('FINANCER', 'Shriram Finance Limited'),
('FINANCER', 'HDB Financial Services'),
('FINANCER', 'L&T Finance')
ON CONFLICT (ref_type, ref_value) DO NOTHING;
