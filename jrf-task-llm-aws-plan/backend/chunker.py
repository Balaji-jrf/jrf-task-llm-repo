"""
chunker.py — page-group chunker for AWS Lambda.

Strategy: group consecutive pages into chunks of PAGE_GROUP_SIZE (default 10).
Each chunk = one DynamoDB item. This keeps chunk count manageable for BM25 search
and gives the LLM a coherent multi-page context window per retrieval hit.

Unchanged from local version except:
- PAGE_GROUP_SIZE constant added (10 pages per chunk, configurable via env var)
- chunk_id uses page_start-page_end instead of per-element hash
"""

import hashlib
import os
import re

PAGE_GROUP_SIZE = int(os.environ.get("PAGE_GROUP_SIZE", "10"))


def _valid_bbox(bbox) -> list[float] | None:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    if not all(0 <= v <= 1 for v in values):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _merge_bboxes(elements: list[dict]) -> list[float] | None:
    boxes = [b for item in elements if (b := _valid_bbox(item.get("bbox")))]
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _chunk_id(document_id: str, page_start: int, page_end: int, text: str) -> str:
    digest = hashlib.sha256(
        f"{document_id}\0{page_start}\0{page_end}\0{text[:200]}".encode()
    ).hexdigest()
    return f"{document_id}-p{page_start}-p{page_end}-{digest[:16]}"


def chunk_document(ocr: dict, page_group_size: int = PAGE_GROUP_SIZE) -> list[dict]:
    """
    Group OCR elements into page-range chunks.
    Each chunk covers `page_group_size` consecutive pages (e.g. pages 1-10, 11-20, …).
    Returns list of chunk dicts ready for DynamoDB write.
    """
    document_id = str(ocr["document_id"])
    filename = str(ocr.get("filename", document_id))
    elements = ocr.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OCR JSON must contain an elements array.")

    # Collect elements per page
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

    if not pages:
        return []

    sorted_pages = sorted(pages)
    total_pages = max(sorted_pages)

    chunks = []
    # Build page groups: [1..group_size], [group_size+1..2*group_size], …
    page_start = 1
    while page_start <= total_pages:
        page_end = page_start + page_group_size - 1
        group_pages = [p for p in sorted_pages if page_start <= p <= page_end]
        if group_pages:
            group_elements = []
            for p in group_pages:
                group_elements.extend(pages[p])
            chunk_text = " ".join(e["text"] for e in group_elements)
            chunks.append({
                "chunk_id": _chunk_id(document_id, page_start, page_end, chunk_text),
                "document_id": document_id,
                "filename": filename,
                "page_start": page_start,
                "page_end": min(page_end, total_pages),
                "page": page_start,          # kept for backward compat with citation display
                "text": chunk_text,
                "bbox": _merge_bboxes(group_elements),
                "element_count": len(group_elements),
            })
        page_start += page_group_size

    return chunks
