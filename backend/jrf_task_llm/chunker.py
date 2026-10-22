import hashlib
import re


def _valid_bbox(bbox) -> list[float] | None:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    if not all(0 <= value <= 1 for value in values):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _merge_bboxes(elements: list[dict]) -> list[float] | None:
    boxes = [box for item in elements if (box := _valid_bbox(item.get("bbox")))]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _stable_id(document_id: str, page: int, text: str) -> str:
    digest = hashlib.sha256(f"{document_id}\0{page}\0{text}".encode()).hexdigest()
    return f"{document_id}-{page}-{digest[:24]}"


def chunk_document(ocr: dict, max_chars: int = 1400) -> list[dict]:
    document_id = str(ocr["document_id"])
    filename = str(ocr.get("filename", document_id))
    elements = ocr.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OCR JSON must contain an elements array.")

    pages: dict[int, list[dict]] = {}
    for element in elements:
        text = re.sub(r"\s+", " ", str(element.get("text", ""))).strip()
        if not text:
            continue
        try:
            page = max(1, int(element.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        pages.setdefault(page, []).append({**element, "text": text})

    chunks = []
    for page in sorted(pages):
        page_elements = pages[page]
        current = []
        current_length = 0
        for element in page_elements:
            text = element["text"]
            if current and current_length + len(text) + 1 > max_chars:
                chunk_text = " ".join(item["text"] for item in current)
                chunks.append({
                    "chunk_id": _stable_id(document_id, page, chunk_text),
                    "document_id": document_id,
                    "filename": filename,
                    "page": page,
                    "text": chunk_text,
                    "bbox": _merge_bboxes(current),
                })
                current = []
                current_length = 0
            current.append(element)
            current_length += len(text) + 1
        if current:
            chunk_text = " ".join(item["text"] for item in current)
            chunks.append({
                "chunk_id": _stable_id(document_id, page, chunk_text),
                "document_id": document_id,
                "filename": filename,
                "page": page,
                "text": chunk_text,
                "bbox": _merge_bboxes(current),
            })
    return chunks
