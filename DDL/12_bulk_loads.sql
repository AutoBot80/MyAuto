-- Bulk loads: tracks Add Sales runs from bulk-uploaded combined PDFs.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS bulk_loads (
    id SERIAL PRIMARY KEY,
    subfolder VARCHAR(128) NOT NULL,
    file_name VARCHAR(256),
    mobile VARCHAR(16),
    name VARCHAR(128),
    folder_path VARCHAR(512),
    result_folder VARCHAR(512),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bulk_loads_created_at ON bulk_loads (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bulk_loads_status ON bulk_loads (status);

COMMENT ON TABLE bulk_loads IS 'Bulk upload Add Sales runs; status Success or Error';
