# Vision: Aadhar scan → ChatGPT

A small module sends an **Aadhar scan image** to **ChatGPT (OpenAI)** and asks it to:
1. **Identify the document** (e.g. Aadhar card).
2. **Locate the customer photo** on the left and return a bounding box (as percentages) so you can crop it later.

ChatGPT returns **text only** (it does not return image bytes). The response is returned as-is so you can see exactly what the model returns.

## Setup

1. **OpenAI API key**  
   In `backend/.env` add:
   ```env
   OPENAI_API_KEY=sk-your-key-here
   ```

2. **Install dependency** (if not already):
   ```cmd
   venv\Scripts\activate.bat
   pip install openai
   ```

## API

**POST** `/vision/aadhar-analyze`  
- **Body:** `multipart/form-data` with one file field: `image` (JPEG or PNG).
- **Response:** JSON with:
  - `content` – Full text reply from ChatGPT (document type + photo region or “Photo region: left_pct=…”).
  - `document_type` – First line of the reply (used as document type).
  - `raw_response` – Full API response (id, model, choices, usage).
  - `error` – Set if something failed (e.g. no API key, bad image).

## Example: what ChatGPT returns

Typical reply in `content`:

```
This is an Aadhar card (Indian national ID).

Photo region: left_pct=5, top_pct=15, width_pct=22, height_pct=28
```

Or:

```
Aadhaar card.

Photo region: not found
```

You can then use the percentages in your own code to crop the image and save the customer picture.

## Calling from the client

Use a form with a file input and POST to `http://localhost:8000/vision/aadhar-analyze` with the image file in the `image` field. The JSON response shows exactly what ChatGPT returned.
