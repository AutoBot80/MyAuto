-- Add classification columns for two-step pipeline: AI classify image, then Tesseract OCR.
-- Run against database: auto_ai

ALTER TABLE ai_reader_queue
ADD COLUMN IF NOT EXISTS document_type VARCHAR(64),
ADD COLUMN IF NOT EXISTS classification_confidence REAL;

COMMENT ON COLUMN ai_reader_queue.document_type IS 'AI classification: e.g. Aadhar card, Driving license, RC, Insurance, Other';
COMMENT ON COLUMN ai_reader_queue.classification_confidence IS 'Confidence score 0-1 from classifier';
