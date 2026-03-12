# Two-step pipeline: Classify → OCR

The AI Reader runs in two steps for each queued document:

1. **Step 1 – Classify:** An AI model classifies the image (e.g. Aadhar card, Driving license, Vehicle RC, Insurance, Other).
2. **Step 2 – OCR:** Tesseract extracts text from the image.

Results are stored in `ai_reader_queue` (`document_type`, `classification_confidence`) and in the flat file (first line: document type and confidence; rest: extracted text).

## Default: stub classifier

By default, **no AI model** is used. The stub classifier returns `document_type = "unknown"` and `confidence = 0`. The pipeline still runs and Tesseract still extracts text. So you get:

- No extra dependencies (no PyTorch/transformers).
- Same OCR output as before; classification is just "unknown".

## Optional: CLIP classifier

To use an **AI model** for Step 1:

1. **Install optional dependencies:**
   ```bash
   pip install transformers torch
   ```

2. **Enable in `backend/.env`:**
   ```env
   USE_AI_CLASSIFIER=true
   ```

3. **Optional – custom labels** (comma-separated):
   ```env
   DOCUMENT_CLASSIFIER_LABELS=Aadhar card,Driving license,Vehicle RC,Insurance,Other
   ```

The app uses **CLIP** (zero-shot image classification) so you don’t need to train a model. Labels are the possible document types; CLIP picks the best match and returns a confidence score.

## Database

Run the migration so the queue table has classification columns:

```bash
psql -h localhost -U postgres -d auto_ai -f DDL/alter/01a_ai_reader_queue_add_classification.sql
```

## Flow

- **Process next / Process all:** For each image we call the classifier → update `document_type` and `classification_confidence` in the queue → run Tesseract → write flat file (with type + text).
- **API responses** and **extractions list** include `document_type` and `classification_confidence`.
- **Queue table** and **AI Reader Queue** UI show a "Type" column.
