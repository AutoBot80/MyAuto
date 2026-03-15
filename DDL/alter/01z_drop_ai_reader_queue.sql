-- Drop ai_reader_queue table.
-- WARNING: This will break OCR queue, uploads that enqueue, and AI Reader Queue page.

DROP TABLE IF EXISTS ai_reader_queue;
