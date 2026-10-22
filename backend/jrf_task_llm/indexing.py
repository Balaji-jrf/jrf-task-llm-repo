from . import chunker, ocr_source, vector_store


def index_document(document_id: str) -> dict:
    document = ocr_source.fetch_ocr_document(document_id)
    chunks = chunker.chunk_document(document)
    if not chunks:
        raise ValueError(f"No text was found in OCR document {document_id}.")
    vector_store.replace_document_chunks(document_id, chunks)
    return {
        "document_id": document_id,
        "filename": document["filename"],
        "chunks_indexed": len(chunks),
    }
