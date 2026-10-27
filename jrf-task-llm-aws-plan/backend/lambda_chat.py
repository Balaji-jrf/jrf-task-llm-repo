"""
lambda_chat.py — jrf-task-llm-chat
Handler: lambda_chat.lambda_handler

Routes:
  POST /api/llm/chat              → start chat/summarization job (async self-invoke)
  GET  /api/llm/chat-jobs/{job_id} → poll job status

Summarization modes (passed in request body as `mode`):
  "chat"           → standard RAG Q&A (default)
  "summarize_doc"  → whole-document summary
  "summarize_chunk"→ summary of a specific chunk range
  "summarize_page" → summary of specific page range
  "summarize_chapter" → chapter-wise summary (uses chapter_hint field)
"""

import json
import logging
import math
import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key

import bedrock_model

_LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CHUNKS_TABLE = os.environ["CHUNKS_TABLE"]
CHATS_TABLE = os.environ["CHATS_TABLE"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
DEFAULT_TOP_K = int(os.environ.get("DEFAULT_TOP_K", "5"))
MAX_TOP_K = int(os.environ.get("MAX_TOP_K", "8"))

_dynamo = boto3.resource("dynamodb")
_lambda_client = boto3.client("lambda")
_chunks_table = _dynamo.Table(CHUNKS_TABLE)
_chats_table = _dynamo.Table(CHATS_TABLE)
_jobs_table = _dynamo.Table(JOBS_TABLE)


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


def _bm25_search(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """Minimal BM25 — identical logic to local vector_store.py."""
    import math
    import re
    tokens = re.findall(r"\w+", query.lower())
    if not tokens:
        return chunks[:top_k]

    df: dict[str, int] = {}
    for chunk in chunks:
        words = set(re.findall(r"\w+", chunk["text"].lower()))
        for word in words:
            df[word] = df.get(word, 0) + 1

    N = len(chunks)
    k1, b = 1.5, 0.75
    avg_len = sum(len(c["text"]) for c in chunks) / max(N, 1)

    scored = []
    for chunk in chunks:
        words = re.findall(r"\w+", chunk["text"].lower())
        word_freq: dict[str, int] = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        score = 0.0
        for token in tokens:
            if token not in df:
                continue
            idf = math.log((N - df[token] + 0.5) / (df[token] + 0.5) + 1)
            tf = word_freq.get(token, 0)
            norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(words) / avg_len))
            score += idf * norm
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def _decimal_to_native(obj):
    """Recursively convert DynamoDB Decimal values to int/float."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_native(i) for i in obj]
    return obj


def _get_chunks_for_documents(document_ids: list[str]) -> list[dict]:
    all_chunks = []
    for doc_id in document_ids:
        resp = _chunks_table.query(
            KeyConditionExpression=Key("document_id").eq(doc_id)
        )
        all_chunks.extend(_decimal_to_native(resp.get("Items", [])))
    return all_chunks


def _get_history(chat_id: str) -> list[dict]:
    if not chat_id:
        return []
    # turn_id is a sort key — filter on it via KeyConditionExpression (begins_with),
    # NOT FilterExpression (which cannot reference primary key attributes).
    resp = _chats_table.query(
        KeyConditionExpression=(
            Key("chat_id").eq(chat_id) & Key("turn_id").begins_with("TURN#")
        ),
    )
    turns = sorted(resp.get("Items", []), key=lambda x: x["turn_id"])
    return [{"question": t.get("question", ""), "answers": t.get("answers", [])} for t in turns]


def _floats_to_decimal(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    from decimal import Decimal
    if isinstance(obj, float):
        return Decimal(str(round(obj, 10)))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _save_turn(chat_id: str, turn_number: int, question: str, answers: list[dict],
               document_ids: list[str], model_ids: list[str], mode: str) -> None:
    turn_id = f"TURN#{turn_number:04d}"
    # Convert floats → Decimal (performance timings etc.) before DynamoDB write
    safe_answers = _floats_to_decimal(answers)
    _chats_table.put_item(Item={
        "chat_id": chat_id,
        "turn_id": turn_id,
        "question": question,
        "answers": safe_answers,
        "document_ids": document_ids,
        "model_ids": model_ids,
        "mode": mode,
        "created_at": int(time.time()),
    })
    # Upsert META record
    _chats_table.update_item(
        Key={"chat_id": chat_id, "turn_id": "META"},
        UpdateExpression=(
            "SET title = if_not_exists(title, :q), "
            "document_ids = :d, model_ids = :m, "
            "updated_at = :t, turn_count = :n"
        ),
        ExpressionAttributeValues={
            ":q": question[:80],
            ":d": document_ids,
            ":m": model_ids,
            ":t": int(time.time()),
            ":n": turn_number,
        },
    )


def _build_summarization_question(mode: str, body: dict) -> str:
    """Convert summarization mode into a natural-language instruction for the LLM."""
    if mode == "summarize_doc":
        return (
            "Please provide a comprehensive summary of the entire document. "
            "Cover the main themes, key findings, and important conclusions."
        )
    if mode == "summarize_chunk":
        chunk_range = body.get("chunk_range", "")
        return (
            f"Please summarize the content from pages {chunk_range}. "
            "Highlight the key points and any important data or arguments."
        )
    if mode == "summarize_page":
        page_range = body.get("page_range", "")
        return (
            f"Please summarize pages {page_range} of the document. "
            "Be concise and capture the essential information."
        )
    if mode == "summarize_chapter":
        chapter_hint = body.get("chapter_hint", "")
        return (
            f"Please provide a chapter-wise summary. "
            f"Focus on: {chapter_hint}. "
            "Identify chapter boundaries from the text and summarise each chapter separately."
        )
    return body.get("question", "")


def _filter_chunks_for_mode(mode: str, body: dict, all_chunks: list[dict]) -> list[dict]:
    """For summarization modes, select relevant chunks instead of BM25 search."""
    if mode == "summarize_doc":
        return all_chunks                          # use everything

    if mode == "summarize_chunk":
        # chunk_range: "1-3" means chunk indices 1 to 3
        chunk_range = str(body.get("chunk_range", ""))
        parts = chunk_range.split("-")
        try:
            start, end = int(parts[0]), int(parts[-1])
            sorted_chunks = sorted(all_chunks, key=lambda c: c.get("page_start", c.get("page", 0)))
            return sorted_chunks[start - 1:end]
        except (ValueError, IndexError):
            return all_chunks[:5]

    if mode in ("summarize_page", "summarize_chapter"):
        page_range = str(body.get("page_range", ""))
        parts = page_range.split("-")
        try:
            p_start, p_end = int(parts[0]), int(parts[-1])
            return [
                c for c in all_chunks
                if c.get("page_start", c.get("page", 0)) <= p_end
                and c.get("page_end", c.get("page", 0)) >= p_start
            ] or all_chunks[:5]
        except (ValueError, IndexError):
            return all_chunks[:5]

    return []   # caller will do BM25


# ── async worker ─────────────────────────────────────────────────────────────

def _run_chat(job_id: str, payload: dict) -> None:
    t_start = time.perf_counter()
    chat_id = payload.get("chat_id") or str(uuid.uuid4())
    document_ids = payload["document_ids"]
    model_ids = payload["model_ids"]
    question_raw = payload.get("question", "")
    mode = payload.get("mode", "chat")
    _LOG.info("[CHAT] job_id=%s chat_id=%s mode=%s models=%s docs=%s",
              job_id, chat_id, mode, model_ids, document_ids)

    question = _build_summarization_question(mode, payload) if mode != "chat" else question_raw

    t0 = time.perf_counter()
    all_chunks = _get_chunks_for_documents(document_ids)
    _LOG.info("[CHAT] chunks_fetched=%d elapsed=%.2fs", len(all_chunks), time.perf_counter() - t0)

    t0 = time.perf_counter()
    history = _get_history(chat_id)
    _LOG.info("[CHAT] history_turns=%d elapsed=%.2fs", len(history), time.perf_counter() - t0)

    # Select context chunks
    if mode == "chat":
        top_k = min(int(payload.get("top_k", DEFAULT_TOP_K)), MAX_TOP_K)
        context_chunks = _bm25_search(question, all_chunks, top_k)
    else:
        context_chunks = _filter_chunks_for_mode(mode, payload, all_chunks)
    _LOG.info("[CHAT] context_chunks=%d", len(context_chunks))

    # Tag each chunk with a source_id for citation display
    for i, chunk in enumerate(context_chunks):
        chunk["source_id"] = f"src{i + 1}"

    answers = []
    for model_id in model_ids:
        t0 = time.perf_counter()
        _LOG.info("[BEDROCK] invoking model_id=%s", model_id)
        answer = bedrock_model.answer_question(
            model_id=model_id,
            question=question,
            context_chunks=context_chunks,
            history=history,
        )
        elapsed = time.perf_counter() - t0
        if answer.get("error"):
            _LOG.error("[BEDROCK] model_id=%s error=%s elapsed=%.2fs", model_id, answer["error"], elapsed)
        else:
            _LOG.info("[BEDROCK] model_id=%s tokens_out=%s elapsed=%.2fs",
                      model_id, answer.get("usage", {}).get("output_tokens", "?"), elapsed)
        answers.append(answer)

    # Determine turn number
    turn_number = len(history) + 1
    _save_turn(chat_id, turn_number, question_raw or question, answers,
               document_ids, model_ids, mode)

    failed = [a for a in answers if a.get("error")]
    status = "complete" if not failed else ("partial" if len(failed) < len(answers) else "failed")
    _LOG.info("[CHAT] DONE job_id=%s status=%s total_elapsed=%.2fs",
              job_id, status, time.perf_counter() - t_start)

    _jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, #r = :r, completed_at = :t",
        ExpressionAttributeNames={"#s": "status", "#r": "result"},
        ExpressionAttributeValues={
            ":s": "completed",
            ":r": _floats_to_decimal({
                "chat_id": chat_id,
                "question": question_raw or question,
                "answers": answers,
                "status": status,
                "mode": mode,
            }),
            ":t": int(time.time()),
        },
    )


# ── handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    # Async worker invocation
    if "job_id" in event and "document_ids" in event and "httpMethod" not in event:
        _run_chat(event["job_id"], event)
        return

    method = event.get("httpMethod", "")

    if method == "OPTIONS":
        return _cors({})

    # POST /api/llm/chat
    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
            document_ids = body.get("document_ids", [])
            model_ids = body.get("model_ids", [])
            mode = body.get("mode", "chat")

            if not document_ids or not model_ids:
                return _cors({"error": "document_ids and model_ids required"}, 400)
            if mode == "chat" and not body.get("question", "").strip():
                return _cors({"error": "question required for chat mode"}, 400)

            job_id = str(uuid.uuid4())
            payload = {**body, "job_id": job_id, "mode": mode}
            _jobs_table.put_item(Item={
                "job_id": job_id,
                "status": "running",
                "created_at": int(time.time()),
            })
            _lambda_client.invoke(
                FunctionName=FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps(payload),
            )
            return _cors({"job_id": job_id, "chat_id": body.get("chat_id", ""), "status": "running"}, 202)
        except Exception as exc:
            _LOG.exception("POST /chat failed")
            return _cors({"error": str(exc)}, 500)

    # GET /api/llm/chat-jobs/{job_id}
    if method == "GET":
        job_id = (event.get("pathParameters") or {}).get("job_id", "")
        if not job_id:
            return _cors({"error": "job_id required"}, 400)
        item = _jobs_table.get_item(Key={"job_id": job_id}).get("Item")
        if not item:
            return _cors({"error": "job not found"}, 404)
        return _cors(item)

    return _cors({"error": "not found"}, 404)
