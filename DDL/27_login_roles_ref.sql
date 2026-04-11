-- Maps logins to roles; optional dealer_id (no FK). Run after login_ref and roles_ref.
-- Run against database: auto_ai
-- Unique (login_id, role_id, dealer_id) with NULL dealer treated as distinct bucket via COALESCE(dealer_id, -1) in index.

CREATE TABLE IF NOT EXISTS login_roles_ref (
    login_roles_ref_id SERIAL PRIMARY KEY,
    login_id VARCHAR(128) NOT NULL,
    role_id INTEGER NOT NULL,
    dealer_id INTEGER,
    CONSTRAINT fk_login_roles_ref_login FOREIGN KEY (login_id) REFERENCES login_ref (login_id) ON DELETE CASCADE,
    CONSTRAINT fk_login_roles_ref_role FOREIGN KEY (role_id) REFERENCES roles_ref (role_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_login_roles_ref_login_role_dealer
    ON login_roles_ref (login_id, role_id, COALESCE(dealer_id, -1));

COMMENT ON TABLE login_roles_ref IS 'Login ↔ role assignments; optional dealer_id (no FK; NULL = not dealer-scoped). Unique index uses COALESCE(dealer_id,-1); use positive dealer_id values only.';
COMMENT ON COLUMN login_roles_ref.dealer_id IS 'Optional; not FK. NULL allowed.';
