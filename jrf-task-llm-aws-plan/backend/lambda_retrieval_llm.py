"""
lambda_retrieval_llm.py — jrf-task-llm-retrieval
Handler: lambda_retrieval_llm.lambda_handler

Routes:
  GET /api/llm/health
  GET /api/llm/models
  GET /api/llm/chats
  GET /api/llm/chats/{chat_id}
  GET /api/llm/documents/{document_id}/chunks   (chunk browser for citation panel)
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
            resp = _chats_table.scan(
                FilterExpression="turn_id = :m",
                ExpressionAttributeValues={":m": "META"},
                ProjectionExpression="chat_id, title, document_ids, model_ids, updated_at, turn_count",
            )
            chats = sorted(
                resp.get("Items", []),
                key=lambda c: c.get("updated_at", 0),
                reverse=True,
            )
            return _cors({"chats": chats})
        except Exception as exc:
            _LOG.exception("GET /chats failed")
            return _cors({"error": str(exc)}, 500)

    # GET /api/llm/chats/{chat_id}
    if "chat_id" in params:
        chat_id = params["chat_id"]
        try:
            resp = _chats_table.query(
                KeyConditionExpression=Key("chat_id").eq(chat_id)
            )
            items = resp.get("Items", [])
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
            resp = _chunks_table.query(
                KeyConditionExpression=Key("document_id").eq(doc_id),
                ProjectionExpression="chunk_id, page_start, page_end, #p, element_count",
                ExpressionAttributeNames={"#p": "page"},
            )
            chunks = sorted(
                resp.get("Items", []),
                key=lambda c: c.get("page_start", c.get("page", 0)),
            )
            return _cors({"document_id": doc_id, "chunks": chunks, "total": len(chunks)})
        except Exception as exc:
            _LOG.exception("GET /documents/%s/chunks failed", doc_id)
            return _cors({"error": str(exc)}, 500)

    return _cors({"error": "not found"}, 404)
