"""Run per-document Q&A and summaries with inline page citations.

Flow:
1. Validate the request and create an asynchronous job.
2. Load indexed chunks and conversation history.
3. Retrieve evidence independently for each selected document.
4. Ask Bedrock, save each document result, and expose it through job polling.

Routes:
  POST /api/llm/chat               Start a chat or summary job.
  GET  /api/llm/chat-jobs/{job_id} Poll the job result.
"""

import json
import logging
import math
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

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
SUPPORTED_MODES = {
    "chat", "summarize_doc", "summarize_chapter", "summarize_page"
}

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
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def _validate_model_selection(model_ids, compare_mode):
    if not isinstance(model_ids, list):
        return False
    expected_models = 2 if compare_mode else 1
    if (
        len(model_ids) != expected_models
        or any(not isinstance(model_id, str) for model_id in model_ids)
        or len(set(model_ids)) != len(model_ids)
        or any(not bedrock_model.is_supported_model(model_id) for model_id in model_ids)
    ):
        return False
    return True


def _bm25_search(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """Return BM25-ranked chunks while representing multiple selected documents."""
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
    selected = []
    selected_ids = set()
    seen_documents = set()
    for score, chunk in scored:
        document_id = chunk.get("document_id")
        if score > 0 and document_id not in seen_documents:
            selected.append(chunk)
            selected_ids.add(chunk.get("chunk_id"))
            seen_documents.add(document_id)
            if len(selected) == top_k:
                return selected
    for _score, chunk in scored:
        if chunk.get("chunk_id") not in selected_ids:
            selected.append(chunk)
            if len(selected) == top_k:
                break
    return selected


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
        kwargs = {"KeyConditionExpression": Key("document_id").eq(doc_id)}
        while True:
            response = _chunks_table.query(**kwargs)
            all_chunks.extend(_decimal_to_native(response.get("Items", [])))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    return all_chunks


def _get_history(chat_id: str) -> list[dict]:
    if not chat_id:
        return []
    # turn_id is a sort key — filter on it via KeyConditionExpression (begins_with),
    # NOT FilterExpression (which cannot reference primary key attributes).
    kwargs = {
        "KeyConditionExpression": (
            Key("chat_id").eq(chat_id) & Key("turn_id").begins_with("TURN#")
        ),
    }
    items = []
    while True:
        response = _chats_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    turns = sorted(items, key=lambda x: x["turn_id"])
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
            "Summarize this document only, using all of its supplied page excerpts. "
            "Organize the answer with concise headings for Overview, Main themes, "
            "Key findings and examples, and Conclusions. Cover important evidence "
            "from across the document, not only its opening pages. Cite each factual "
            "sentence or bullet immediately with the source marker for its page. "
            "Do not put citations together at the end."
        )
    if mode == "summarize_page":
        page_range = body.get("page_range", "")
        return (
            f"Summarize pages {page_range} of this document only. Organize the result "
            "with concise headings for Overview, Key points, and Conclusions. Cite "
            "each factual sentence or bullet immediately with its page source marker. "
            "Do not put citations together at the end."
        )
    if mode == "summarize_chapter":
        return (
            "Create a chapter-wise summary of this document using all supplied page "
            "excerpts. Identify chapter titles and boundaries from the excerpts, then "
            "summarize every identified chapter separately. Do not focus on a single "
            "chapter or omit other chapters. Do not invent chapter names or details. "
            "Organize the result with a heading for each chapter followed by concise "
            "key points. Cite each factual sentence or bullet immediately with its "
            "page source marker; never collect citations at the end."
        )
    return body.get("question", "")


def _chunks_for_document_mode(mode: str, body: dict, question: str,
                              chunks: list[dict]) -> list[dict]:
    """Choose evidence independently inside one selected document."""
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (
            chunk.get("page", chunk.get("page_start", 0)),
            chunk.get("chunk_id", ""),
        ),
    )
    if mode == "chat":
        top_k = max(1, min(int(body.get("top_k", DEFAULT_TOP_K)), MAX_TOP_K))
        return _bm25_search(question, ordered_chunks, top_k)
    if mode == "summarize_page":
        page_bounds = _parse_page_range(body.get("page_range", ""))
        if not page_bounds:
            return []
        page_start, page_end = page_bounds
        return [
            chunk for chunk in ordered_chunks
            if chunk.get("page_start", chunk.get("page", 0)) <= page_end
            and chunk.get("page_end", chunk.get("page", 0)) >= page_start
        ]
    if mode == "summarize_chapter":
        return ordered_chunks
    return ordered_chunks


def _parse_page_range(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*(?:[-–—]\s*(\d+)\s*)?", str(value))
    if not match:
        return None
    page_start = int(match.group(1))
    page_end = int(match.group(2) or page_start)
    if page_start < 1 or page_end < page_start:
        return None
    return page_start, page_end


def _answer_each_document(model_id: str, document_ids: list[str],
                          all_chunks: list[dict], mode: str, body: dict,
                          question: str, history: list[dict]) -> dict:
    """Answer or summarize each selected document with isolated evidence and citations."""
    def answer_document(document_id):
        document_chunks = sorted(
            (chunk for chunk in all_chunks if chunk.get("document_id") == document_id),
            key=lambda chunk: (
                chunk.get("page", chunk.get("page_start", 0)),
                chunk.get("chunk_id", ""),
            ),
        )
        filename = (
            next(
                (chunk.get("filename") for chunk in document_chunks if chunk.get("filename")),
                None,
            )
            or document_id
        )
        chunks = _chunks_for_document_mode(mode, body, question, document_chunks)
        if not chunks:
            message = (
                f"No indexed content found in the requested page range for {filename}."
                if mode == "summarize_page"
                else f"No indexed content found for {filename}."
            )
            return {
                "document_id": document_id,
                "filename": filename,
                "error": message,
            }

        for index, chunk in enumerate(chunks, start=1):
            chunk["source_id"] = f"src{index}"

        _LOG.info("[ANSWER] model=%s mode=%s document=%s chunks=%d",
                  model_id, mode, document_id, len(chunks))
        document_question = question
        document_history = []
        if mode == "chat":
            document_history = [
                {
                    "question": turn.get("question", ""),
                    "answers": [
                        {"text": result.get("text", "")}
                        for answer in turn.get("answers", [])
                        for result in answer.get("document_summaries", [])
                        if result.get("document_id") == document_id and result.get("text")
                    ],
                }
                for turn in history
            ]
            document_history = [
                turn for turn in document_history if turn["answers"]
            ][-4:]
            document_question = (
                "Answer the user's question using only the supplied excerpts from this "
                f"document ({filename}). Organize the answer into a short direct answer "
                "followed by key supporting points when useful. Cite each factual "
                f"sentence or bullet immediately using this document's page markers.\n\n"
                f"Question: {question}"
            )
        answer = bedrock_model.answer_question(
            model_id=model_id,
            question=document_question,
            context_chunks=chunks,
            history=document_history,
        )
        return {
            "document_id": document_id,
            "filename": filename,
            "text": answer.get("text", ""),
            "citations": answer.get("citations", []),
            "usage": answer.get("usage", {}),
            "performance": answer.get("performance", {}),
            **({"error": answer["error"]} if answer.get("error") else {}),
        }

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(document_ids)))) as executor:
        document_summaries = list(
            executor.map(answer_document, document_ids)
        )

    failures = [item for item in document_summaries if item.get("error")]
    model_info = next(
        (model for model in bedrock_model.BEDROCK_MODELS if model["model_id"] == model_id),
        {},
    )
    return {
        "model_id": model_id,
        "display_name": model_info.get("display_name", model_id),
        "document_summaries": document_summaries,
        "usage": {
            key: sum(item.get("usage", {}).get(key, 0) for item in document_summaries)
            for key in ("input_tokens", "output_tokens")
        },
        "performance": {
            "inference_seconds": round(sum(
                item.get("performance", {}).get("inference_seconds", 0)
                for item in document_summaries
            ), 2),
        },
        **({"error": "Summary failed for every selected document."}
           if failures and len(failures) == len(document_summaries) else {}),
    }


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
    indexed_document_ids = {chunk.get("document_id") for chunk in all_chunks}
    missing_document_ids = [doc_id for doc_id in document_ids if doc_id not in indexed_document_ids]
    if missing_document_ids:
        raise ValueError(
            "These documents are not indexed yet: "
            + ", ".join(missing_document_ids)
            + ". Prepare the selected documents before chatting."
        )

    t0 = time.perf_counter()
    history = _get_history(chat_id)
    _LOG.info("[CHAT] history_turns=%d elapsed=%.2fs", len(history), time.perf_counter() - t0)

    answers = []
    for model_id in model_ids:
        t0 = time.perf_counter()
        _LOG.info("[BEDROCK] invoking model_id=%s", model_id)
        answer = _answer_each_document(
            model_id=model_id,
            document_ids=document_ids,
            all_chunks=all_chunks,
            mode=mode,
            body=payload,
            question=question,
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

    document_results = [
        item
        for answer in answers
        for item in answer.get("document_summaries", [])
    ]
    successful_results = sum(not item.get("error") for item in document_results)
    failed_results = sum(bool(item.get("error")) for item in document_results)
    status = "complete" if not failed_results else (
        "partial" if successful_results else "failed"
    )
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
        try:
            _run_chat(event["job_id"], event)
        except Exception as exc:
            _LOG.exception("[CHAT] worker failed job_id=%s", event["job_id"])
            _jobs_table.update_item(
                Key={"job_id": event["job_id"]},
                UpdateExpression="SET #s = :s, #e = :e, completed_at = :t",
                ExpressionAttributeNames={"#s": "status", "#e": "error"},
                ExpressionAttributeValues={
                    ":s": "failed",
                    ":e": str(exc)[:500],
                    ":t": int(time.time()),
                },
            )
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
            if (
                not isinstance(document_ids, list)
                or not document_ids
                or len(document_ids) > 5
                or any(not isinstance(doc_id, str) or not doc_id.strip() for doc_id in document_ids)
            ):
                return _cors({"error": "document_ids must contain between 1 and 5 document IDs"}, 400)
            document_ids = list(dict.fromkeys(doc_id.strip() for doc_id in document_ids))

            if not model_ids:
                return _cors({"error": "document_ids and model_ids required"}, 400)
            if mode not in SUPPORTED_MODES:
                return _cors({"error": "Unsupported chat mode"}, 400)
            if mode == "summarize_page" and not _parse_page_range(body.get("page_range", "")):
                return _cors({"error": "page_range must be a page number or range such as 5-20"}, 400)
            question = body.get("question", "")
            if mode == "chat" and (not isinstance(question, str) or not question.strip()):
                return _cors({"error": "question required for chat mode"}, 400)
            compare_mode = body.get("compare_mode", False)
            if not isinstance(compare_mode, bool):
                return _cors({"error": "compare_mode must be a boolean"}, 400)
            if not _validate_model_selection(model_ids, compare_mode):
                return _cors({
                    "error": (
                        "Select exactly one supported model for chat."
                        if not compare_mode
                        else "Select exactly two different supported models for comparison."
                    )
                }, 400)

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
