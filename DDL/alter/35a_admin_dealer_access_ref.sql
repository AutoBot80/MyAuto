-- Admin Saathi: which dealers each login may view in Admin Usage / Dealers tab.
-- Run after: dealer_ref (04b), login_ref (26), login_roles_ref (27), roles_ref (25).

CREATE TABLE IF NOT EXISTS admin_dealer_access_ref (
    admin_dealer_access_id SERIAL PRIMARY KEY,
    login_id VARCHAR(128) NOT NULL,
    dealer_id INTEGER NOT NULL,
    CONSTRAINT fk_admin_dealer_access_login FOREIGN KEY (login_id) REFERENCES login_ref (login_id) ON DELETE CASCADE,
    CONSTRAINT fk_admin_dealer_access_dealer FOREIGN KEY (dealer_id) REFERENCES dealer_ref (dealer_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_dealer_access_login_dealer
    ON admin_dealer_access_ref (login_id, dealer_id);

COMMENT ON TABLE admin_dealer_access_ref IS
  'Admin Saathi scope: login_id may view Usage matrices, folders, and Dealers tab for listed dealer_id rows only.';
COMMENT ON COLUMN admin_dealer_access_ref.login_id IS 'login_ref.login_id';
COMMENT ON COLUMN admin_dealer_access_ref.dealer_id IS 'dealer_ref.dealer_id';

-- Bootstrap from existing admin role assignments (login_roles_ref + roles_ref.admin_flag).
INSERT INTO admin_dealer_access_ref (login_id, dealer_id)
SELECT DISTINCT lrr.login_id, lrr.dealer_id
FROM login_roles_ref lrr
INNER JOIN roles_ref rr ON rr.role_id = lrr.role_id
WHERE COALESCE(rr.admin_flag, '') = 'Y'
  AND lrr.dealer_id IS NOT NULL
ON CONFLICT DO NOTHING;
