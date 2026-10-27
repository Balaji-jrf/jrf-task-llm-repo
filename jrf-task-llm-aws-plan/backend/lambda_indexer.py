"""
lambda_indexer.py — jrf-task-llm-indexer
Handler: lambda_indexer.lambda_handler

Routes:
  POST /api/llm/documents/index          → start indexing job (async self-invoke)
  GET  /api/llm/documents/index/{job_id} → poll job status

Fix: OCR API /document/{id} returns metadata + presigned URLs only.
     The actual OCR JSON (with elements[]) lives at ocr_presigned_url.
     _fetch_ocr_document() now does the two-step fetch matching ocr_source.py.
"""

import json
import logging
import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key

from chunker import chunk_document

_LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CHUNKS_TABLE    = os.environ["CHUNKS_TABLE"]           # jrf-task-llm-Chunks
JOBS_TABLE      = os.environ["JOBS_TABLE"]             # jrf-task-llm-Jobs
OCR_API_BASE_URL = os.environ["OCR_API_BASE_URL"].rstrip("/")
ALLOWED_ORIGIN  = os.environ.get("ALLOWED_ORIGIN", "*")
FUNCTION_NAME   = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

_dynamo        = boto3.resource("dynamodb")
_lambda_client = boto3.client("lambda")
_chunks_table  = _dynamo.Table(CHUNKS_TABLE)
_jobs_table    = _dynamo.Table(JOBS_TABLE)


# ── helpers ──────────────────────────────────────────────────────────────────

def _cors(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def _http_get_json(url: str) -> dict:
    """Fetch a URL and return parsed JSON. Raises on HTTP or parse errors."""
    import urllib.request
    _LOG.info("[FETCH] GET %s", url[:120])
    with urllib.request.urlopen(url, timeout=60) as resp:
        raw = resp.read()
    _LOG.info("[FETCH] response size=%d bytes", len(raw))
    return json.loads(raw)


def _fetch_ocr_document(document_id: str) -> dict:
    """
    Two-step fetch matching ocr_source.py:
      1. GET /document/{id}  → metadata (status, filename, ocr_presigned_url)
      2. GET ocr_presigned_url → actual OCR JSON with elements[]
    """
    meta_url = f"{OCR_API_BASE_URL}/document/{document_id}"
    _LOG.info("[OCR] Step 1 — fetching document metadata for %s", document_id)
    metadata = _http_get_json(meta_url)

    _LOG.info(
        "[OCR] metadata keys=%s  status=%s  filename=%s",
        list(metadata.keys()),
        metadata.get("status"),
        metadata.get("filename"),
    )

    if metadata.get("status") != "completed":
        raise ValueError(
            f"Document {document_id} is not completed (status={metadata.get('status')})"
        )

    ocr_url = metadata.get("ocr_presigned_url")
    if not ocr_url:
        raise ValueError(
            f"Document {document_id} has no ocr_presigned_url. "
            f"Available keys: {list(metadata.keys())}"
        )

    _LOG.info("[OCR] Step 2 — fetching OCR JSON from presigned URL")
    ocr_data = _http_get_json(ocr_url)

    _LOG.info(
        "[OCR] OCR JSON keys=%s  element_count=%s",
        list(ocr_data.keys()),
        len(ocr_data.get("elements", [])) if isinstance(ocr_data.get("elements"), list) else "N/A",
    )

    # Merge document_id and filename so chunker always has them
    return {
        **ocr_data,
        "document_id": document_id,
        "filename": metadata.get("filename") or ocr_data.get("filename") or document_id,
    }


def _floats_to_decimal(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    from decimal import Decimal, ROUND_HALF_UP
    if isinstance(obj, float):
        # Round to 10dp to avoid DynamoDB precision issues
        return Decimal(str(round(obj, 10)))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _write_chunks(chunks: list[dict]) -> None:
    _LOG.info("[DDB] Writing %d chunks to %s", len(chunks), CHUNKS_TABLE)
    with _chunks_table.batch_writer() as batch:
        for chunk in chunks:
            # Filter nulls, convert floats → Decimal (DynamoDB requirement)
            item = _floats_to_decimal({k: v for k, v in chunk.items() if v is not None})
            batch.put_item(Item=item)
    _LOG.info("[DDB] Batch write complete")


def _delete_existing_chunks(document_id: str) -> None:
    _LOG.info("[DDB] Deleting existing chunks for document_id=%s", document_id)
    resp = _chunks_table.query(
        KeyConditionExpression=Key("document_id").eq(document_id),
        ProjectionExpression="document_id, chunk_id",
    )
    items = resp.get("Items", [])
    _LOG.info("[DDB] Found %d existing chunks to delete", len(items))
    with _chunks_table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"document_id": item["document_id"], "chunk_id": item["chunk_id"]})


# ── async worker ─────────────────────────────────────────────────────────────

def _run_indexing(job_id: str, document_ids: list[str]) -> None:
    _LOG.info("[JOB] Starting indexing job_id=%s for %d documents: %s",
              job_id, len(document_ids), document_ids)
    indexed = []
    errors  = []

    for doc_id in document_ids:
        _LOG.info("[DOC] Processing document_id=%s", doc_id)
        try:
            ocr = _fetch_ocr_document(doc_id)

            elements = ocr.get("elements")
            _LOG.info(
                "[DOC] OCR fetched — document_id=%s  filename=%s  elements_type=%s  elements_count=%s",
                doc_id,
                ocr.get("filename"),
                type(elements).__name__,
                len(elements) if isinstance(elements, list) else "N/A",
            )

            _delete_existing_chunks(doc_id)
            chunks = chunk_document(ocr)
            _LOG.info("[DOC] Chunked — document_id=%s  chunks=%d", doc_id, len(chunks))

            _write_chunks(chunks)
            indexed.append({"document_id": doc_id, "chunks_indexed": len(chunks)})
            _LOG.info("[DOC] SUCCESS document_id=%s  chunks_indexed=%d", doc_id, len(chunks))

        except Exception as exc:
            _LOG.exception("[DOC] FAILED document_id=%s  error=%s", doc_id, exc)
            errors.append({"document_id": doc_id, "error": str(exc)})

    final_status = "completed" if not errors else ("partial" if indexed else "failed")
    _LOG.info("[JOB] Finished job_id=%s  status=%s  indexed=%d  errors=%d",
              job_id, final_status, len(indexed), len(errors))

    _jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, indexed_documents = :i, errors = :e, completed_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": final_status,
            ":i": indexed,
            ":e": errors,
            ":t": int(time.time()),
        },
    )


# ── handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    _LOG.info("[HANDLER] event keys=%s", list(event.keys()))

    # Async worker invocation (no httpMethod)
    if "job_id" in event and "document_ids" in event and "httpMethod" not in event:
        _LOG.info("[HANDLER] Async worker invocation — job_id=%s", event["job_id"])
        _run_indexing(event["job_id"], event["document_ids"])
        return

    method = event.get("httpMethod", "")
    path   = event.get("path", "")
    _LOG.info("[HANDLER] HTTP %s %s", method, path)

    if method == "OPTIONS":
        return _cors({})

    # POST /api/llm/documents/index
    if method == "POST":
        try:
            body         = json.loads(event.get("body") or "{}")
            document_ids = body.get("document_ids", [])
            _LOG.info("[POST] index request document_ids=%s", document_ids)

            if not document_ids:
                return _cors({"error": "document_ids required"}, 400)

            job_id = str(uuid.uuid4())
            _LOG.info("[POST] Creating job job_id=%s", job_id)
            _jobs_table.put_item(Item={
                "job_id":       job_id,
                "status":       "running",
                "document_ids": document_ids,
                "created_at":   int(time.time()),
            })

            _LOG.info("[POST] Async-invoking self FunctionName=%s", FUNCTION_NAME)
            _lambda_client.invoke(
                FunctionName=FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({"job_id": job_id, "document_ids": document_ids}),
            )
            _LOG.info("[POST] Async invoke dispatched job_id=%s", job_id)
            return _cors({"job_id": job_id, "status": "running"}, 202)

        except Exception as exc:
            _LOG.exception("[POST] /index failed: %s", exc)
            return _cors({"error": str(exc)}, 500)

    # GET /api/llm/documents/index/{job_id}
    if method == "GET":
        job_id = (event.get("pathParameters") or {}).get("job_id", "")
        _LOG.info("[GET] polling job_id=%s", job_id)
        if not job_id:
            return _cors({"error": "job_id required"}, 400)
        item = _jobs_table.get_item(Key={"job_id": job_id}).get("Item")
        if not item:
            _LOG.warning("[GET] job not found job_id=%s", job_id)
            return _cors({"error": "job not found"}, 404)
        _LOG.info("[GET] job_id=%s  status=%s", job_id, item.get("status"))
        return _cors(item)

    _LOG.warning("[HANDLER] Unmatched route method=%s path=%s", method, path)
    return _cors({"error": "not found"}, 404)
