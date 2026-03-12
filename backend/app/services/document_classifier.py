"""Step 1 of the pipeline: classify document image (e.g. Aadhar, DL, RC) before OCR."""

from typing import Protocol

# Type for PIL Image - avoid importing here so callers can pass PIL.Image
from typing import Any


class DocumentClassifier(Protocol):
    """Classify a document image into a known type. Returns (document_type, confidence 0-1)."""

    def classify(self, image: Any) -> tuple[str, float]:
        ...


class StubClassifier:
    """No-op classifier when AI model is not used or not installed. Returns 'unknown' and 0."""

    def classify(self, image: Any) -> tuple[str, float]:
        return ("unknown", 0.0)


def _make_clip_classifier() -> DocumentClassifier | None:
    """Build CLIP-based zero-shot classifier if transformers/torch are available."""
    try:
        from transformers import pipeline
    except ImportError:
        return None
    from app.config import DOCUMENT_CLASSIFIER_LABELS

    pipe = pipeline(
        "zero-shot-image-classification",
        model="openai/clip-vit-base-patch32",
    )
    labels = [l.strip() for l in DOCUMENT_CLASSIFIER_LABELS if l.strip()]

    class ClipClassifier:
        def classify(self, image: Any) -> tuple[str, float]:
            if not labels:
                return ("unknown", 0.0)
            result = pipe(image, candidate_labels=labels)
            if not result:
                return ("unknown", 0.0)
            best = result[0]
            return (best.get("label", "unknown"), float(best.get("score", 0.0)))

    return ClipClassifier()


def get_document_classifier(use_ai: bool = False) -> DocumentClassifier:
    """Return the document classifier. If use_ai=True, use CLIP when available; else stub."""
    if use_ai:
        classifier = _make_clip_classifier()
        if classifier is not None:
            return classifier
    return StubClassifier()
