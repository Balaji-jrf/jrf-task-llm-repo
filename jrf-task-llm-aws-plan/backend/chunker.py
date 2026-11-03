"""Create page-local searchable chunks from OCR text and Textract tables."""

import hashlib
import os
import re

MAX_CHUNK_CHARS = int(os.environ.get("MAX_CHUNK_CHARS", "2800"))
CHUNKER_VERSION = 2


def _valid_bbox(bbox):
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


def _merge_bboxes(items):
    boxes = [
        box
        for item in items
        if (box := _valid_bbox(item.get("bbox"))) is not None
    ]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _page_number(item):
    try:
        return max(1, int(item.get("page", 1)))
    except (TypeError, ValueError):
        return 1


def _content_units(ocr):
    units_by_page = {}
    elements = ocr.get("elements", [])
    for element in elements if isinstance(elements, list) else []:
        if not isinstance(element, dict):
            continue
        text = re.sub(r"\s+", " ", str(element.get("text") or "")).strip()
        if text:
            units_by_page.setdefault(_page_number(element), []).append({
                "text": text,
                "bbox": _valid_bbox(element.get("bbox")),
                "content_type": "text",
            })

    tables = ocr.get("tables", [])
    table_number = 0
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows", [])
        if not isinstance(rows, list):
            continue
        table_number += 1
        page = _page_number(table)
        for row in rows:
            if not isinstance(row, list):
                continue
            cells = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
            if not any(cells):
                continue
            units_by_page.setdefault(page, []).append({
                "text": f"Table {table_number} row: " + " | ".join(cells),
                "bbox": _valid_bbox(table.get("bbox")),
                "content_type": "table",
            })

    for units in units_by_page.values():
        units.sort(key=lambda unit: (
            unit["bbox"][1] if unit["bbox"] else 1.0,
            unit["bbox"][0] if unit["bbox"] else 0.0,
        ))
    return units_by_page


def _split_unit(unit, max_chars):
    text = unit["text"]
    if len(text) <= max_chars:
        return [unit]
    parts = []
    current = ""
    for word in text.split():
        if len(word) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.extend(word[index:index + max_chars] for index in range(0, len(word), max_chars))
            continue
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return [{**unit, "text": part} for part in parts]


def _chunk_id(document_id, page, index, text):
    digest = hashlib.sha256(f"{page}\0{index}\0{text}".encode()).hexdigest()[:16]
    return f"{document_id}-p{page:04d}-c{index:03d}-{digest}"


def chunk_document(ocr, source_hash="", max_chars=None):
    """Return bounded chunks that never cross a PDF page boundary."""
    document_id = str(ocr.get("document_id") or "")
    if not document_id:
        raise ValueError("OCR JSON must contain a document_id.")
    if max_chars is None:
        max_chars = MAX_CHUNK_CHARS
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError) as error:
        raise ValueError("max_chars must be a positive integer.") from error
    if max_chars < 1:
        raise ValueError("max_chars must be a positive integer.")

    filename = str(ocr.get("doc_name") or ocr.get("filename") or document_id)
    doc_tag = str(ocr.get("doc_tag") or "")
    units_by_page = _content_units(ocr)
    chunks = []
    for page in sorted(units_by_page):
        page_chunks = []
        current_units = []
        current_text = ""
        for original_unit in units_by_page[page]:
            for unit in _split_unit(original_unit, max_chars):
                candidate = f"{current_text}\n{unit['text']}".strip()
                if current_units and len(candidate) > max_chars:
                    page_chunks.append((current_units, current_text))
                    current_units = []
                    current_text = ""
                    candidate = unit["text"]
                current_units.append(unit)
                current_text = candidate
        if current_units:
            page_chunks.append((current_units, current_text))

        for index, (items, text) in enumerate(page_chunks, start=1):
            content_types = {item["content_type"] for item in items}
            content_type = next(iter(content_types)) if len(content_types) == 1 else "mixed"
            chunk = {
                "chunk_id": _chunk_id(document_id, page, index, text),
                "document_id": document_id,
                "filename": filename,
                "doc_tag": doc_tag,
                "page": page,
                "page_start": page,
                "page_end": page,
                "text": text,
                "content_type": content_type,
                "element_count": len(items),
                "chunker_version": CHUNKER_VERSION,
            }
            if source_hash:
                chunk["source_hash"] = source_hash
            bbox = _merge_bboxes(items)
            if bbox:
                chunk["bbox"] = bbox
            chunks.append(chunk)
    return chunks
