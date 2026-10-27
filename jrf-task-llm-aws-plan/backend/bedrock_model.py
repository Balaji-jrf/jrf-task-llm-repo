"""
bedrock_model.py — replaces local_model.py.

Same answer_question() signature and output dict shape.
Swaps ChatLlamaCpp / GGUF inference for Amazon Bedrock invoke_model().
"""

import json
import logging
import os
import time

import boto3

_LOG = logging.getLogger(__name__)

BEDROCK_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Catalogue of available Bedrock models (mirrors local GGUF catalogue shape)
BEDROCK_MODELS = [
    {
        "model_id": "amazon.nova-lite-v1:0",
        "display_name": "Nova Lite",
        "description": "Fast, cost-efficient — good for quick factual questions",
    },
    {
        "model_id": "amazon.nova-pro-v1:0",
        "display_name": "Nova Pro",
        "description": "Balanced — detailed research answers",
    },
    {
        "model_id": "meta.llama3-1-8b-instruct-v1:0",
        "display_name": "Llama 3.1 8B",
        "description": "Open-weight alternative for comparison",
    },
]

_bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


def list_models() -> list[dict]:
    """Return the catalogue — same shape as local_model.list_models()."""
    return [
        {
            "model_id": m["model_id"],
            "display_name": m["display_name"],
            "description": m["description"],
        }
        for m in BEDROCK_MODELS
    ]


def _build_prompt(question: str, context_chunks: list[dict], history: list[dict]) -> tuple:
    """Build Bedrock messages. Truncates each chunk to keep context focused."""
    system = (
        "You are a precise research assistant for the BREATHE project. "
        "You are given excerpts from OCR-scanned documents. "
        "Your job is to answer the question by carefully reading the provided excerpts "
        "and extracting SPECIFIC information — names, places, organizations, dates, numbers — "
        "directly from the text. "
        "Do NOT generate generic or background knowledge answers. "
        "If the answer is in the text, quote or list it specifically. "
        "If something is not in the excerpts, say so. "
        "Always cite which source_id each piece of information came from."
    )

    # Truncate each chunk to 3000 chars — enough for specific facts, avoids flooding context
    MAX_CHUNK_CHARS = 3000
    context_parts = []
    for c in context_chunks:
        text = c["text"]
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS] + "… [truncated]"
        context_parts.append(
            f"[{c['source_id']}] {c['filename']} "
            f"(pages {c.get('page_start', c.get('page', '?'))}–{c.get('page_end', c.get('page', '?'))}):\n"
            f"{text}"
        )
    context_text = "\n\n---\n\n".join(context_parts)
    user_content = f"Document excerpts:\n\n{context_text}\n\n---\n\nQuestion: {question}"

    # Nova/Bedrock requires content as an array of content blocks
    messages = []
    for turn in history[-4:]:  # last 2 turns only — keep context window tight
        messages.append({"role": "user", "content": [{"text": turn["question"]}]})
        if turn.get("answers"):
            best = turn["answers"][0]
            answer_text = best.get("text", "") or ""
            if answer_text:
                messages.append({"role": "assistant", "content": [{"text": answer_text[:500]}]})
    messages.append({"role": "user", "content": [{"text": user_content}]})

    return system, messages


def answer_question(
    model_id: str,
    question: str,
    context_chunks: list[dict],
    history: list[dict] | None = None,
) -> dict:
    """
    Invoke Bedrock and return the same output dict shape as local_model.answer_question().
    """
    history = history or []
    system_prompt, messages = _build_prompt(question, context_chunks, history)

    model_info = next((m for m in BEDROCK_MODELS if m["model_id"] == model_id), None)
    display_name = model_info["display_name"] if model_info else model_id

    t0 = time.perf_counter()
    try:
        body = {
            "messages": messages,
            "system": [{"text": system_prompt}],
            "inferenceConfig": {"maxTokens": 1500, "temperature": 0.1},
        }
        response = _bedrock.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        elapsed = time.perf_counter() - t0

        # Extract text — Nova returns content blocks like [{"text": "..."}] (no "type" field)
        # Llama returns {"generation": "..."}
        _LOG.info("[BEDROCK] raw result keys=%s", list(result.keys()))
        output_content = result.get("output", {}).get("message", {}).get("content", [])
        _LOG.info("[BEDROCK] output_content blocks=%d", len(output_content))
        # Accept any block that has a "text" key regardless of "type"
        text = " ".join(
            block["text"] for block in output_content if isinstance(block, dict) and block.get("text")
        ).strip()
        if not text:
            # Llama / older models
            text = result.get("generation", result.get("content", "")).strip()
        _LOG.info("[BEDROCK] extracted text length=%d", len(text))

        usage = result.get("usage", {})
        return {
            "model_id": model_id,
            "display_name": display_name,
            "text": text,
            "citations": [
                {
                    "source_id": c["source_id"],
                    "document_id": c["document_id"],
                    "filename": c["filename"],
                    "page": c.get("page_start", c["page"]),
                    "page_start": c.get("page_start", c["page"]),
                    "page_end": c.get("page_end", c["page"]),
                    "bbox": c.get("bbox"),
                    "text": c["text"][:300],
                }
                for c in context_chunks
            ],
            "sources": [],
            "usage": {
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            },
            "performance": {
                "model_load_seconds": 0,
                "inference_seconds": round(elapsed, 2),
                "output_tokens_per_second": round(
                    usage.get("outputTokens", 0) / max(elapsed, 0.001), 1
                ),
            },
        }
    except Exception as exc:
        _LOG.exception("Bedrock invoke failed for %s", model_id)
        return {
            "model_id": model_id,
            "display_name": display_name,
            "error": str(exc),
            "citations": [],
            "sources": [],
        }
