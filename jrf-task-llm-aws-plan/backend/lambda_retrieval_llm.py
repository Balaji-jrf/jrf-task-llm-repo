"""Serve app metadata, chat history, and persisted document chunks.

Flow:
1. Handle CORS preflight and allow GET routes only.
2. Resolve a health, model, chat-history, index-status, or chunk request.
3. Query DynamoDB and return the response expected by the React app.

Routes:
  GET /api/llm/health
  GET /api/llm/models
  GET /api/llm/chats
  GET /api/llm/chats/{chat_id}
  GET /api/llm/documents/{document_id}/chunks
"""

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

import bedrock_model

_LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CHUNKS_TABLE = os.environ["CHUNKS_TABLE"]
CHATS_TABLE = os.environ["CHATS_TABLE"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

_dynamo = boto3.resource("dynamodb")
_chunks_table = _dynamo.Table(CHUNKS_TABLE)
_chats_table = _dynamo.Table(CHATS_TABLE)


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


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return _cors({})

    if method != "GET":
        return _cors({"error": "method not allowed"}, 405)

    # GET /api/llm/health
    if path.endswith("/health"):
        return _cors({"status": "ok", "service": "jrf-task-llm"})

    # GET /api/llm/models
    if path.endswith("/models"):
        return _cors({"models": bedrock_model.list_models()})

    # GET /api/llm/chats
    if path.endswith("/chats") and "chat_id" not in params:
        try:
            chats, kwargs = [], {
                "FilterExpression": "turn_id = :m",
                "ExpressionAttributeValues": {":m": "META"},
                "ProjectionExpression": "chat_id, title, document_ids, model_ids, updated_at, turn_count",
            }
            while True:
                resp = _chats_table.scan(**kwargs)
                chats.extend(resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
            chats.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
            return _cors({"chats": chats})
        except Exception as exc:
            _LOG.exception("GET /chats failed")
            return _cors({"error": str(exc)}, 500)

    # GET /api/llm/chats/{chat_id}
    if "chat_id" in params:
        chat_id = params["chat_id"]
        try:
            items, kwargs = [], {
                "KeyConditionExpression": Key("chat_id").eq(chat_id)
            }
            while True:
                resp = _chats_table.query(**kwargs)
                items.extend(resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
            meta = next((i for i in items if i["turn_id"] == "META"), {})
            turns = sorted(
                [i for i in items if i["turn_id"].startswith("TURN#")],
                key=lambda x: x["turn_id"],
            )
            return _cors({
                "chat": meta,
                "turns": [
                    {"question": t.get("question", ""), "answers": t.get("answers", []), "mode": t.get("mode", "chat")}
                    for t in turns
                ],
            })
        except Exception as exc:
            _LOG.exception("GET /chats/%s failed", chat_id)
            return _cors({"error": str(exc)}, 500)

    # GET /api/llm/documents/{document_id}/chunks
    if "document_id" in params:
        doc_id = params["document_id"]
        try:
            status_only = (
                (event.get("queryStringParameters") or {}).get("status_only", "").lower()
                in {"1", "true", "yes"}
            )
            if status_only:
                count, kwargs = 0, {
                    "KeyConditionExpression": Key("document_id").eq(doc_id),
                    "Select": "COUNT",
                }
                while True:
                    resp = _chunks_table.query(**kwargs)
                    count += resp.get("Count", 0)
                    last_key = resp.get("LastEvaluatedKey")
                    if not last_key:
                        break
                    kwargs["ExclusiveStartKey"] = last_key
                return _cors({
                    "document_id": doc_id,
                    "total": count,
                    "chunk_count": count,
                    "index_status": "DONE" if count else "NOT_INDEXED",
                })

            chunks, kwargs = [], {
                "KeyConditionExpression": Key("document_id").eq(doc_id),
                "ProjectionExpression": "chunk_id, page_start, page_end, #p, content_type, element_count, doc_tag",
                "ExpressionAttributeNames": {"#p": "page"},
            }
            while True:
                resp = _chunks_table.query(**kwargs)
                chunks.extend(resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
            chunks.sort(key=lambda c: (c.get("page_start", c.get("page", 0)), c.get("chunk_id", "")))
            return _cors({
                "document_id": doc_id,
                "chunks": chunks,
                "total": len(chunks),
                "index_status": "DONE" if chunks else "NOT_INDEXED",
                "chunk_count": len(chunks),
                "doc_tag": chunks[0].get("doc_tag", "") if chunks else "",
            })
        except Exception as exc:
            _LOG.exception("GET /documents/%s/chunks failed", doc_id)
            return _cors({"error": str(exc)}, 500)

    return _cors({"error": "not found"}, 404)
