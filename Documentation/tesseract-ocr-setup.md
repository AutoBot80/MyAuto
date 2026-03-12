# Tesseract OCR Setup (including Hindi for Aadhar)

The AI Reader uses **Tesseract** with **English + Hindi** so that Aadhar scans (and other documents with Devanagari text) are read correctly.

## 1. Default language

The backend uses `OCR_LANG=eng+hin` by default (set in `backend/app/config.py`, overridable via env var). This enables:

- **eng** – English (numbers, Latin text on Aadhar)
- **hin** – Hindi (Devanagari script, e.g. name in Hindi on Aadhar)

## 2. Installing Hindi language data (required for Hindi text)

If Hindi text is not recognised, Tesseract is likely using only English. You need the **Hindi trained data** (`hin.traineddata`).

### Windows

1. Find your Tesseract install folder, e.g.  
   `C:\Program Files\Tesseract-OCR`
2. Open the **tessdata** folder:  
   `C:\Program Files\Tesseract-OCR\tessdata`
3. Download Hindi data:
   - Go to: https://github.com/tesseract-ocr/tessdata/raw/main/hin.traineddata  
   - Or: https://github.com/tesseract-ocr/tessdata_best/raw/main/hin.traineddata (better accuracy, larger file)
4. Save the file as `hin.traineddata` inside the **tessdata** folder.
5. Restart the backend (or process-all) and re-run OCR on the Aadhar scan.

### Linux (apt)

```bash
sudo apt install tesseract-ocr-hin
```

### macOS (Homebrew)

```bash
brew install tesseract-lang  # includes Hindi
# or only Hindi:
brew install tesseract
# then add hin.traineddata to tessdata (see GitHub links above)
```

## 3. Overriding languages

To use only English (no Hindi):

- In `backend/.env` add:  
  `OCR_LANG=eng`

To add more Indic languages (e.g. Marathi, Bengali), install the corresponding `.traineddata` into `tessdata` and set:

- `OCR_LANG=eng+hin+mar`  
  (use Tesseract’s 3-letter codes)

## 4. Verifying

From the project root (with Tesseract on PATH):

```bash
tesseract --list-langs
```

You should see `eng` and `hin` in the list.

---

## 5. Logos and pictures in scans

**Tesseract only extracts text.** It does not recognise or describe logos, photos, or graphics. It just finds text in the image. So:

- **Text on or near logos/pictures** – We improve this by:
  - **Page Segmentation Mode (PSM):** `OCR_PSM=3` (default) lets Tesseract detect layout automatically, which helps on pages with mixed content (text + logos/graphics). In `backend/.env` you can set `OCR_PSM=6` for a single text block or `OCR_PSM=11` for sparse text.
  - **Preprocessing:** With `OCR_PREPROCESS=true` (default), images are converted to grayscale and contrast is boosted before OCR, which often helps when logos or backgrounds are present.

- **Actually “reading” logos or pictures** (e.g. “this is the Aadhar logo”, “this is a face”) – That is not OCR. You need a **vision** API or model, for example:
  - **Google Cloud Vision API** (object/localisation + text)
  - **AWS Rekognition** (labels, text)
  - **Azure Computer Vision**
  - Or a local model (e.g. object detection / image classification) if you want to run it on your server.
