from pydantic import BaseModel


class ProcessNextResponse(BaseModel):
    """Result of processing one queue item: Step 1 classify, Step 2 Tesseract OCR."""

    id: int
    subfolder: str
    filename: str
    status: str
    error: str | None = None
    extracted_text: str | None = None
    output_path: str | None = None
    document_type: str | None = None
    classification_confidence: float | None = None


class ExtractionItem(BaseModel):
    """Queue item with extracted text from flat file."""

    id: int
    subfolder: str
    filename: str
    status: str
    document_type: str | None = None
    classification_confidence: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    extracted_text: str | None = None
    output_path: str


class ProcessStatusResponse(BaseModel):
    """Status of the background process that reads all queued documents."""

    status: str  # "waiting" | "running" | "sleeping"
    processed_count: int = 0
    last_error: str | None = None
