"""Grounded document answering through Amazon Bedrock Converse."""

import logging
import os
import re
import time

import boto3

_LOG = logging.getLogger(__name__)
_INLINE_CITATION = re.compile(
    r"\[(src\d+(?:\s*,\s*src\d+)*)(?:\s*,?\s*(?:page|p)\s*[:=.]?\s*\d+)?\]",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "about", "after", "also", "and", "are", "because", "been", "being", "but",
    "can", "could", "does", "for", "from", "had", "has", "have", "into", "its",
    "more", "most", "not", "our", "over", "said", "should", "than", "that",
    "the", "their", "them", "there", "these", "they", "this", "those", "through",
    "under", "was", "were", "which", "while", "with", "would",
}

DEFAULT_BEDROCK_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Catalogue of available Bedrock models (mirrors local GGUF catalogue shape)
BEDROCK_MODELS = [
    {
        "model_id": "amazon.nova-lite-v1:0",
        "display_name": "Nova Lite",
        "description": "Fast, cost-efficient — good for quick factual questions",
        "region": "ap-southeast-2",
    },
    {
        "model_id": "amazon.nova-pro-v1:0",
        "display_name": "Nova Pro",
        "description": "Balanced — detailed research answers",
        "region": "ap-southeast-2",
    },
    {
        "model_id": "meta.llama3-1-8b-instruct-v1:0",
        "display_name": "Llama 3.1 8B",
        "description": "Open-weight alternative (US West region)",
        "region": "us-west-2",
    },
]

_bedrock_clients = {}


def _bedrock_client(model_id):
    model = next((entry for entry in BEDROCK_MODELS if entry["model_id"] == model_id), None)
    if model is None:
        raise ValueError(f"Unsupported Bedrock model: {model_id}")
    region = model.get("region", DEFAULT_BEDROCK_REGION)
    if region not in _bedrock_clients:
        _bedrock_clients[region] = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_clients[region]


def is_supported_model(model_id):
    return any(model["model_id"] == model_id for model in BEDROCK_MODELS)


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
    """Build Bedrock messages with page-specific evidence and citation instructions."""
    system = (
        "You are a precise research assistant for the BREATHE project. "
        "You are given excerpts from OCR-scanned documents. "
        "Your job is to answer the question by carefully reading the provided excerpts "
        "and extracting SPECIFIC information — names, places, organizations, dates, numbers — "
        "directly from the text. "
        "Do NOT generate generic or background knowledge answers. "
        "If the answer is in the text, quote or list it specifically. "
        "If something is not in the excerpts, say so. "
        "CITATION RULES: put source markers immediately after the sentence or bullet "
        "they support, for example [src1] or [src1, src3]. Never collect citations at "
        "the end of the answer or in a separate references list. Cite every factual "
        "paragraph and every factual bullet. Cite only source IDs whose text directly "
        "supports that claim. Do not cite a page range when a single source page is "
        "available. If no excerpt supports a claim, say the documents do not provide "
        "the information; do not guess. Return source markers exactly as [src1], "
        "[src2], etc.; do not omit markers from factual statements."
    )

    context_parts = []
    for c in context_chunks:
        page = c.get("page", c.get("page_start", "?"))
        content_type = c.get("content_type", "text")
        context_parts.append(
            f"[{c['source_id']}] {c['filename']} (document_id={c['document_id']}, "
            f"page={page}, type={content_type}):\n{c['text']}"
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


def _citation_ids(value: str) -> list[str]:
    return [
        source_id.strip().lower()
        for marker in _INLINE_CITATION.findall(value)
        for source_id in marker.split(",")
    ]


def _best_source_id(sentence: str, chunks_by_source: dict[str, dict],
                    preferred_source_ids: list[str],
                    trust_explicit_marker: bool = False) -> str | None:
    words = {
        word for word in re.findall(r"\w+", sentence.lower())
        if len(word) > 2 and word not in _STOP_WORDS and not word.isdigit()
    }
    if not words:
        return None

    def rank(source_ids: list[str]) -> list[tuple[float, int, str]]:
        candidates = []
        for source_id in source_ids:
            chunk = chunks_by_source[source_id]
            chunk_words = {
                word for word in re.findall(r"\w+", str(chunk.get("text", "")).lower())
                if len(word) > 2 and word not in _STOP_WORDS and not word.isdigit()
            }
            overlap = len(words & chunk_words)
            if overlap:
                candidates.append((overlap / len(words), overlap, source_id))
        return candidates

    preferred = list(dict.fromkeys(
        source_id for source_id in preferred_source_ids if source_id in chunks_by_source
    ))
    candidates = rank(preferred) if preferred else []
    if not candidates and preferred and trust_explicit_marker:
        return preferred[0]
    if not candidates:
        candidates = rank(list(chunks_by_source))
    return max(candidates)[2] if candidates else None


def _add_inline_citations(text: str, context_chunks: list[dict]) -> str:
    """Keep model citations with claims and add a best-evidence citation if omitted."""
    chunks_by_source = {
        str(chunk.get("source_id", "")).lower(): chunk
        for chunk in context_chunks
        if chunk.get("source_id")
    }
    if not chunks_by_source:
        return text

    model_sources = [
        source_id for source_id in dict.fromkeys(_citation_ids(text))
        if source_id in chunks_by_source
    ]
    output_lines = []
    for line in text.splitlines():
        if _INLINE_CITATION.fullmatch(line.strip()):
            continue
        if not line.strip() or re.match(r"^\s{0,3}#{1,6}\s", line):
            output_lines.append(line)
            continue

        marker_ids = [
            source_id for source_id in dict.fromkeys(_citation_ids(line))
            if source_id in chunks_by_source
        ]
        line_without_markers = _INLINE_CITATION.sub("", line).rstrip()
        bullet = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$", line_without_markers)
        prefix, content = (bullet.group(1), bullet.group(2)) if bullet else ("", line_without_markers)
        sentences = re.split(r"(?<=[.!?])\s+(?=[\"'“‘(\[]*[A-Z0-9])", content)
        formatted = []
        for index, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue
            source_id = _best_source_id(
                sentence,
                chunks_by_source,
                marker_ids or model_sources,
                trust_explicit_marker=bool(marker_ids),
            )
            marker = f" [{source_id}]" if source_id else ""
            formatted.append((prefix if index == 0 else "") + sentence + marker)
        output_lines.extend(formatted or [line])
    return "\n".join(output_lines)


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
        response = _bedrock_client(model_id).converse(
            modelId=model_id,
            messages=messages,
            system=[{"text": system_prompt}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.1},
        )
        result = response
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

        text = _add_inline_citations(text, context_chunks)
        usage = result.get("usage", {})
        cited_source_ids = set(_citation_ids(text))
        return {
            "model_id": model_id,
            "display_name": display_name,
            "text": text,
            "citations": [
                {
                    "source_id": c["source_id"],
                    "document_id": c["document_id"],
                    "filename": c["filename"],
                    "page": c.get("page", c.get("page_start", 1)),
                    "page_start": c.get("page", c.get("page_start", 1)),
                    "page_end": c.get("page", c.get("page_end", 1)),
                    "bbox": c.get("bbox"),
                    "content_type": c.get("content_type", "text"),
                    "text": c["text"],
                }
                for c in context_chunks
                if c.get("source_id", "").lower() in cited_source_ids
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
