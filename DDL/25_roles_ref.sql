-- Role reference: home-module access flags per role (Sales Window, RTO Desk, Service, Admin, Dealer).
-- No FK dependencies. Preserved by Admin "Delete All Data" (public tables whose name LIKE '%ref'; see admin router).
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS roles_ref (
    role_id SERIAL PRIMARY KEY,
    role_name VARCHAR(128) NOT NULL,
    pos_flag CHAR(1) NOT NULL DEFAULT 'N',
    rto_flag CHAR(1) NOT NULL DEFAULT 'N',
    service_flag CHAR(1) NOT NULL DEFAULT 'N',
    admin_flag CHAR(1) NOT NULL DEFAULT 'N',
    dealer_flag CHAR(1) NOT NULL DEFAULT 'N',
    CONSTRAINT uq_roles_ref_role_name UNIQUE (role_name),
    CONSTRAINT chk_roles_ref_pos_flag CHECK (pos_flag IN ('Y', 'N')),
    CONSTRAINT chk_roles_ref_rto_flag CHECK (rto_flag IN ('Y', 'N')),
    CONSTRAINT chk_roles_ref_service_flag CHECK (service_flag IN ('Y', 'N')),
    CONSTRAINT chk_roles_ref_admin_flag CHECK (admin_flag IN ('Y', 'N')),
    CONSTRAINT chk_roles_ref_dealer_flag CHECK (dealer_flag IN ('Y', 'N'))
);

COMMENT ON TABLE roles_ref IS 'Role names with Y/N access to each home module tile; preserved on admin reset-all-data';
COMMENT ON COLUMN roles_ref.role_name IS 'Display name of the role';
COMMENT ON COLUMN roles_ref.pos_flag IS 'Y/N: Sales Window (POS) tile';
COMMENT ON COLUMN roles_ref.rto_flag IS 'Y/N: RTO Desk tile';
COMMENT ON COLUMN roles_ref.service_flag IS 'Y/N: Service tile';
COMMENT ON COLUMN roles_ref.admin_flag IS 'Y/N: Admin tile';
COMMENT ON COLUMN roles_ref.dealer_flag IS 'Y/N: Dealer tile';
