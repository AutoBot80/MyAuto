from pydantic import BaseModel


class QueuedItem(BaseModel):
    id: int
    subfolder: str
    filename: str
    status: str
    created_at: str | None = None

    class Config:
        from_attributes = True


class UploadScansResponse(BaseModel):
    saved_count: int
    saved_files: list[str]
    saved_to: str
    queued_items: list[dict]
