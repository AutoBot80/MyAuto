-- User accounts (login). login_id is user-chosen and the primary key.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS login_ref (
    login_id VARCHAR(128) NOT NULL,
    pwd_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(32),
    email VARCHAR(255),
    active_flag CHAR(1) NOT NULL DEFAULT 'Y',
    CONSTRAINT login_ref_pkey PRIMARY KEY (login_id),
    CONSTRAINT chk_login_ref_active_flag CHECK (active_flag IN ('Y', 'N'))
);

COMMENT ON TABLE login_ref IS 'User logins; pwd_hash stores hash only (e.g. argon2/bcrypt)';
COMMENT ON COLUMN login_ref.login_id IS 'User-supplied login id; primary key';
COMMENT ON COLUMN login_ref.pwd_hash IS 'Password hash; never store plaintext';
COMMENT ON COLUMN login_ref.email IS 'Optional; not unique';
COMMENT ON COLUMN login_ref.active_flag IS 'Y = active, N = disabled';
