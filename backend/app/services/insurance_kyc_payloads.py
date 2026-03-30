"""Minimal PNG payloads for insurance KYC file inputs when the portal requires document uploads."""
import base64

_MIN_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def insurance_kyc_png_payloads() -> list[dict]:
    return [
        {"name": "aadhar_front.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
        {"name": "aadhar_rear.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
        {"name": "customer_photo_aadhar_front.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
    ]
