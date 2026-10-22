import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

from .config import OCR_API_BASE_URL


class OcrSourceError(RuntimeError):
    pass


def _get_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=25) as response:
            return json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OcrSourceError(f"OCR source request failed: {exc}") from exc


def fetch_ocr_document(document_id: str) -> dict:
    if not OCR_API_BASE_URL:
        raise OcrSourceError("OCR_API_BASE_URL is not configured.")
    document = _get_json(
        f"{OCR_API_BASE_URL.rstrip('/')}/document/{quote(document_id, safe='')}"
    )
    if document.get("status") != "completed":
        raise OcrSourceError(f"OCR document {document_id} is not completed.")
    ocr_url = document.get("ocr_presigned_url")
    if not ocr_url:
        raise OcrSourceError(f"OCR document {document_id} has no OCR JSON URL.")
    ocr_data = _get_json(ocr_url)
    return {
        **ocr_data,
        "document_id": document_id,
        "filename": document.get("filename") or ocr_data.get("filename") or document_id,
    }
