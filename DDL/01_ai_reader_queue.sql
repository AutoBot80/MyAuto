-- Queue for OCR/AI reader processing of uploaded scans.
-- Run against database: auto_ai

CREATE TABLE IF NOT EXISTS ai_reader_queue (
    id SERIAL PRIMARY KEY,
    subfolder TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    document_type VARCHAR(64),
    classification_confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ai_reader_queue IS 'Queue for Tesseract/OCR; status: queued, processing, done, failed';
COMMENT ON COLUMN ai_reader_queue.document_type IS 'AI classification: e.g. Aadhar card, Driving license, RC, Insurance, Other';
COMMENT ON COLUMN ai_reader_queue.classification_confidence IS 'Confidence score 0-1 from classifier';
