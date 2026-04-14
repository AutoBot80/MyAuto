-- Seed general insurers for ref_type INSURER. Run after DDL/28_master_ref.sql
-- PowerShell (from repo root): psql -U postgres -d auto_ai -f "DDL\seed_master_ref_insurers.sql"

INSERT INTO master_ref (ref_type, ref_value) VALUES
('INSURER', 'HDFC ERGO General Insurance'),
('INSURER', 'Tata AIG General Insurance'),
('INSURER', 'Bajaj Allianz General Insurance'),
('INSURER', 'ICICI Lombard General Insurance'),
('INSURER', 'Go Digit General Insurance'),
('INSURER', 'Acko General Insurance'),
('INSURER', 'Cholamandalam MS General Insurance'),
('INSURER', 'Reliance General Insurance'),
('INSURER', 'IFFCO Tokio General Insurance'),
('INSURER', 'Zurich Kotak General Insurance'),
('INSURER', 'Future Generali India Insurance'),
('INSURER', 'Royal Sundaram General Insurance'),
('INSURER', 'Bharti AXA General Insurance'),
('INSURER', 'Liberty General Insurance'),
('INSURER', 'Magma HDI General Insurance'),
('INSURER', 'Raheja QBE General Insurance'),
('INSURER', 'Shriram General Insurance'),
('INSURER', 'Universal Sompo General Insurance'),
('INSURER', 'Navi General Insurance'),
('INSURER', 'The New India Assurance Co. Ltd.'),
('INSURER', 'The Oriental Insurance Co. Ltd.'),
('INSURER', 'National Insurance Co. Ltd.'),
('INSURER', 'United India Insurance Co. Ltd.'),
('INSURER', 'SBI General Insurance')
ON CONFLICT (ref_type, ref_value) DO NOTHING;
