# AWS Textract for Details Sheet

The **AI Reader Queue** pipeline uses **AWS Textract in forms mode only** to scan the **Details sheet** (e.g. `Details.jpg`). Aadhar scans are not processed by this pipeline; separate Python code will handle Aadhar later.

**Languages:** Textract supports **English, French, German, Italian, Portuguese, Spanish** only. **Hindi is not supported.**

**Mode:** The pipeline uses **AnalyzeDocument** with **FORMS** and **TABLES** to extract key-value pairs and full text. Output is written to `backend/ocr_output/` and shown in the AI Reader Queue (extracted text and document type "Details sheet").

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
2. Attach permission: **AmazonTextractFullAccess** (or a custom policy that allows `textract:AnalyzeDocument` and `textract:DetectDocumentText`).
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

Stop the FastAPI/uvicorn process and start it again so it reloads `.env`.

**Alternative:** If you use AWS CLI and already ran `aws configure`, you can rely on the default profile instead of `.env` (boto3 will use `~/.aws/credentials`). For the app, the simplest is to add the two variables above to `backend/.env`.

## Pipeline behavior

- **Queue:** Uploaded files (Aadhar + Details) are added to the AI Reader Queue.
- **Process:** “Process all” (or process-next) runs **Textract (forms)** only on queue items whose **filename contains "details"** (e.g. `Details.jpg`). Other files (e.g. `Aadhar.jpg`) remain queued; they are not processed by this pipeline.
- **Output:** For each processed Details sheet, key-value pairs and full text are written to a `.txt` file under `backend/ocr_output/` and the queue row is updated with status `done` and document type `Details sheet`.

## API Endpoints

### POST `/textract/extract`

Upload a document image (JPEG/PNG, max 5 MB). Returns plain text extraction (DetectDocumentText). Useful for testing.

### POST `/textract/extract-forms`

Upload a document image. Returns **full_text** and **key_value_pairs** (AnalyzeDocument with FORMS + TABLES). This is the same logic used by the AI Reader Queue pipeline for the Details sheet.

### GET `/textract/extract-from-queue?subfolder=...&filename=...&forms=true`

Run Textract on a file already under "Uploaded scans". Use `forms=true` for forms/key-value output.

## Aadhar

Aadhar scans are not processed by the current pipeline. Separate Python code will be added later to handle Aadhar (e.g. with Tesseract + Hindi or another approach).
