# AWS Textract for Details Sheet

Use AWS Textract to extract text from the Sales Detail Sheet (or any document image) and compare with Tesseract output.

## Setup

### 1. Install dependency (backend)

From project root, using the project’s venv:

```cmd
cd "c:\Users\arya_\OneDrive\Desktop\My Auto.AI"
venv\Scripts\pip.exe install boto3
```

### 2. AWS credentials (“unable to locate credentials” fix)

The **backend** needs AWS credentials to call Textract. Add them to **`backend/.env`** (same file as `DATABASE_URL`).

**Step A – Create an IAM user and keys (if you don’t have them):**

1. Log in to **AWS Console** → **IAM** → **Users** → **Create user** (e.g. name: `textract-user`).
2. Attach permission: **AmazonTextractFullAccess** (or a custom policy that allows `textract:DetectDocumentText`).
3. After the user is created: **Security credentials** → **Access keys** → **Create access key** → choose “Application running outside AWS” → create. Copy the **Access key ID** and **Secret access key** (you won’t see the secret again).

**Step B – Put credentials in `backend/.env`:**

Open **`backend/.env`** and add these lines (use your real key and secret):

```env
AWS_ACCESS_KEY_ID=AKIA......................
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_REGION=ap-south-1
```

- No quotes, no spaces around `=`.
- If your password or secret contains `#`, avoid putting it inside a comment.

**Step C – Restart the backend**

Stop the FastAPI/uvicorn process and start it again so it reloads `.env`. Then run “Choose file & run Textract” again from the client.

**Alternative:** If you use AWS CLI and already ran `aws configure`, you can rely on the default profile instead of `.env` (boto3 will use `~/.aws/credentials`). For the app, the simplest is to add the two variables above to `backend/.env`.

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
