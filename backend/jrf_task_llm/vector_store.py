from pathlib import Path
import re
from threading import RLock

from .config import LOCAL_DATA_DIR
from .local_storage import read_json, write_json

LOCAL_CHUNKS_FILE = Path(LOCAL_DATA_DIR) / "chunks.json"
_LOCAL_LOCK = RLock()


def replace_document_chunks(document_id: str, chunks: list[dict]) -> None:
    with _LOCAL_LOCK:
        records = _read_local_chunks()
        records = {
            chunk_id: record
            for chunk_id, record in records.items()
            if record.get("document_id") != document_id
        }
        for chunk in chunks:
            if chunk.get("document_id") != document_id:
                raise ValueError("Chunk document IDs must match the replacement document.")
            chunk_id = chunk.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError("Each local chunk must have a non-empty chunk ID.")
            records[chunk_id] = {
                "document_id": document_id,
                "filename": str(chunk["filename"]),
                "page": int(chunk["page"]),
                "text": str(chunk["text"]),
                "bbox": chunk.get("bbox"),
            }
        write_json(LOCAL_CHUNKS_FILE, records)


def search_chunks(query: str, document_ids: list[str], top_k: int) -> list[dict]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Local retrieval requires a non-empty text query.")
    with _LOCAL_LOCK:
        records = _read_local_chunks()
    return _rank_local_chunks(query, records, set(document_ids), top_k)


def _rank_local_chunks(
    query: str,
    records: dict[str, dict],
    document_ids: set[str],
    top_k: int,
) -> list[dict]:
    query_terms = _terms(query)
    if not query_terms:
        return []

    candidates = [
        (chunk_id, record, _terms(record.get("text", "")))
        for chunk_id, record in records.items()
        if record.get("document_id") in document_ids
    ]
    if not candidates:
        return []

    document_frequency = {
        term: sum(term in set(terms) for _, _, terms in candidates)
        for term in query_terms
    }
    average_length = sum(len(terms) for _, _, terms in candidates) / len(candidates)
    average_length = max(average_length, 1)
    candidate_count = len(candidates)
    scores = []
    for chunk_id, record, terms in candidates:
        term_frequency = {}
        for term in terms:
            term_frequency[term] = term_frequency.get(term, 0) + 1
        document_length = len(terms)
        score = 0.0
        for term in query_terms:
            frequency = term_frequency.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = (
                1
                + (candidate_count - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * document_length / average_length
            )
            score += (
                query_terms.count(term)
                * (inverse_frequency ** 0.5)
                * frequency
                * 2.5
                / denominator
            )
        if score:
            scores.append({
                key: record[key]
                for key in ("document_id", "filename", "page", "text", "bbox")
            } | {"score": score, "chunk_id": chunk_id})
    scores.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    return scores[:top_k]


def _terms(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _read_local_chunks() -> dict[str, dict]:
    records = read_json(LOCAL_CHUNKS_FILE, {})
    if not isinstance(records, dict) or any(
        not isinstance(key, str) or not isinstance(value, dict)
        for key, value in records.items()
    ):
        raise ValueError(f"Local chunk store is malformed: {LOCAL_CHUNKS_FILE}")
    return records
