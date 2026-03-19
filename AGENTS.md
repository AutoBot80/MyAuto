# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

Dealer Saathi is an Auto Dealer Management System with two services:

| Service | Directory | Port | Command |
|---------|-----------|------|---------|
| **FastAPI backend** | `backend/` | 8000 | `source backend/venv/bin/activate && uvicorn app.main:app --reload --port 8000` (from repo root) |
| **React/Vite frontend** | `client/` | 5173 | `npm run dev` (from `client/`) |

The Vite dev server proxies all API routes to the backend (see `client/vite.config.ts`).

### Database

- **PostgreSQL 16** on localhost:5432, database `auto_ai`, user `postgres`, password `postgres`.
- `backend/.env` must contain `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/auto_ai`.
- DDL scripts live in `DDL/`. Run base scripts then `DDL/alter/` scripts then seed scripts (see `DDL/README.md` for order).
- Start PostgreSQL if not running: `sudo pg_ctlcluster 16 main start`.

### Backend gotchas

- The Python backend runs inside a virtualenv at `backend/venv/`. Always activate it before running uvicorn or pip commands.
- `python-multipart` is a required runtime dependency not listed in `requirements.txt`; it is installed in the venv.
- Tesseract OCR (`tesseract-ocr`, `tesseract-ocr-eng`, `tesseract-ocr-hin`) is installed system-wide for document OCR features.
- `libcairo2-dev` is required at build time for `pycairo` (dependency of `xhtml2pdf`).

### Frontend

- Package manager: **npm** (lockfile: `client/package-lock.json`).
- Lint: `npm run lint` (ESLint). Build check: `npx tsc -b --noEmit`. Both have pre-existing warnings/errors in the codebase.
- Dev server: `npm run dev` from `client/`.

### Optional services (not required for core CRUD)

- **OpenAI API** (`OPENAI_API_KEY`): Vision-based Aadhar card analysis.
- **AWS Textract** (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`): Alternative OCR.
- **Playwright/Chromium**: DMS and Vahan browser automation (`playwright install chromium`).
