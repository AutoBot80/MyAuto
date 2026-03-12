# AWS Textract for Details Sheet

Use AWS Textract to extract text from the Sales Detail Sheet (or any document image) and compare with Tesseract output.

## Setup

1. **Install dependency** (backend):
   ```bash
   pip install boto3
   ```

2. **AWS credentials** (one of):
   - Environment: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optionally `AWS_REGION` (default `ap-south-1`).
   - Or use default AWS CLI profile (`aws configure`).

3. **Optional** in `backend/.env`:
   ```env
   AWS_REGION=ap-south-1
   ```

## API Endpoints

### POST `/textract/extract`

Upload a document image (JPEG/PNG, max 5 MB). Returns:

- **full_text**: All detected lines concatenated with newlines.
- **blocks**: List of blocks (BlockType, Text, Confidence) from Textract.
- **raw_response**: Summary (e.g. BlockCount).
- **error**: Set if something failed (e.g. missing credentials, file too large).

**Example (curl):**
```bash
curl -X POST "http://127.0.0.1:8000/textract/extract" \
  -F "file=@path/to/Details.jpg"
```

**Example (Swagger):** Open `http://127.0.0.1:8000/docs`, find `POST /textract/extract`, choose a file, execute, and see the response.

### GET `/textract/extract-from-queue?subfolder=...&filename=...`

Run Textract on a file already under "Uploaded scans". Useful to test on a queue file (e.g. a Details.jpg) without uploading again.

**Example:**
```text
GET /textract/extract-from-queue?subfolder=9876543210_100325&filename=Details.jpg
```

## Comparing with Tesseract

- **Tesseract**: Used by the AI Reader Queue pipeline; output is in `backend/ocr_output/` and in the queue’s extracted text.
- **Textract**: Call the endpoints above to get `full_text` and `blocks` for the same image and compare readability and structure.

If Textract gives better results, the pipeline can be extended to use Textract for specific document types (e.g. "Sales Detail Sheet" or "Details.jpg") instead of or in addition to Tesseract.
