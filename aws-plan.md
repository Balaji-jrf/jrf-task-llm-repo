# LLM RAG Migration Plan — Local → AWS Bedrock + Cloud
## jrf-task-llm-repo

**Status:** Planned — not started  
**Goal:** Migrate the local Python/GGUF RAG system to fully serverless AWS.  
**Principle:** Nothing new — exact 1-to-1 replacement of every local component with an AWS equivalent. Same logic, same API routes, same frontend.

---

## Current local stack

```
localhost:8787  (Python HTTP server)
    │
    ├── local_server.py       HTTP API, routes, validation
    ├── indexing.py           fetch OCR → chunk → store
    ├── chunker.py            page-aware text chunking
    ├── vector_store.py       BM25 search over chunks.json
    ├── chat_graph.py         LangGraph: retrieve → answer → persist
    ├── local_model.py        load GGUF via llama.cpp, run inference
    ├── chat_store.py         read/write chats.json
    ├── local_storage.py      atomic JSON file helpers
    ├── ocr_source.py         read OCR API (unchanged)
    └── config.py             env config (unchanged)

Storage:
    ~/.local/share/jrf-task-llm/chunks.json   ← indexed text chunks
    ~/.local/share/jrf-task-llm/chats.json    ← conversation history

Models (GGUF files on disk, CPU inference):
    Qwen2.5-1.5B-Instruct-Q4_K_M
    Qwen2.5-7B
    DeepSeek-R1-Distill-Qwen-7B
    Meta-Llama-3.1-8B
```

---

## Component mapping — Local → AWS

| Local | AWS Replacement | Notes |
|---|---|---|
| `local_server.py` (localhost:8787) | API Gateway + 3 Lambda functions | Same routes, same request/response shapes |
| `local_model.py` + GGUF files | `bedrock_model.py` + Amazon Bedrock | Same prompt structure, same output format |
| `chunks.json` | DynamoDB `jrf-task-llm-Chunks` | Same schema, BM25 logic unchanged |
| `chats.json` | DynamoDB `jrf-task-llm-Chats` | Same turn structure |
| `local_storage.py` | Removed | No local files needed |
| `chunker.py` | Unchanged | Zero code changes |
| `chat_graph.py` | Unchanged | Swap `local_model` → `bedrock_model` only |
| `chat_store.py` | Unchanged logic | DynamoDB writes instead of JSON |
| `vector_store.py` | Unchanged logic | DynamoDB reads instead of JSON |
| `ocr_source.py` | Unchanged | Still reads from existing OCR API |
| `config.py` | Updated env vars | Remove local paths, add AWS config |

---

## AWS resources to create

### DynamoDB tables

```
jrf-task-llm-Chunks
  Partition key : document_id  (String)
  Sort key      : chunk_id     (String)
  Attributes    : filename, page, text, bbox
  Billing       : PAY_PER_REQUEST
  Region        : ap-south-1 (Mumbai)

jrf-task-llm-Chats
  Partition key : chat_id   (String)
  Sort key      : turn_id   (String)
                  "META"       → chat header (title, model_ids, timestamps)
                  "TURN#001"   → first turn, "TURN#002" second, etc.
  Attributes    : question, answers, retrieved_sources, timestamps, model_ids
  Billing       : PAY_PER_REQUEST
  Region        : ap-south-1 (Mumbai)
```

### Lambda functions (3 total)

```
jrf-task-llm-indexer
  Handler  : lambda_indexer.lambda_handler
  Routes   : POST /api/llm/documents/index
             GET  /api/llm/documents/index/{job_id}
  Logic    : ocr_source → chunker → DynamoDB Chunks write
  Timeout  : 120s
  Memory   : 512 MB

jrf-task-llm-chat
  Handler  : lambda_chat.lambda_handler
  Routes   : POST /api/llm/chat
             GET  /api/llm/chat-jobs/{job_id}
  Logic    : DynamoDB Chunks BM25 search → Bedrock invoke → DynamoDB Chats write
  Timeout  : 60s
  Memory   : 512 MB

jrf-task-llm-retrieval
  Handler  : lambda_retrieval_llm.lambda_handler
  Routes   : GET  /api/llm/health
             GET  /api/llm/models
             GET  /api/llm/chats
             GET  /api/llm/chats/{chat_id}
  Logic    : Read-only DynamoDB queries + list Bedrock models
  Timeout  : 30s
  Memory   : 256 MB
```

### API Gateway

```
Reuse existing: jrf-task-ocr-api (o0dqnexpsg)
Add new routes under /api/llm/* prefix
Same CORS config as existing routes
Region: ap-south-1 (Mumbai) — after Sydney → Mumbai migration
```

---

## Bedrock model mapping

| Local GGUF | Bedrock Model ID | Speed | Use case |
|---|---|---|---|
| Qwen2.5-1.5B (fast) | `amazon.nova-lite-v1:0` | ~1s | Quick answers, already in use |
| Qwen2.5-7B (balanced) | `amazon.nova-pro-v1:0` | ~3s | Detailed research answers |
| DeepSeek-R1-Distill-Qwen-7B | `amazon.nova-pro-v1:0` | ~3s | Same tier as above |
| Meta-Llama-3.1-8B | `meta.llama3-1-8b-instruct-v1:0` | ~2s | Alternative model for comparison |

> Note: Check Bedrock model availability in ap-south-1 before migration.
> Nova Lite is confirmed available. Nova Pro and Llama need verification.

---

## Files to write (build order)

### Step 1 — DynamoDB tables
```bash
# Create jrf-task-llm-Chunks
aws dynamodb create-table \
  --table-name jrf-task-llm-Chunks \
  --attribute-definitions \
    AttributeName=document_id,AttributeType=S \
    AttributeName=chunk_id,AttributeType=S \
  --key-schema \
    AttributeName=document_id,KeyType=HASH \
    AttributeName=chunk_id,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1

# Create jrf-task-llm-Chats
aws dynamodb create-table \
  --table-name jrf-task-llm-Chats \
  --attribute-definitions \
    AttributeName=chat_id,AttributeType=S \
    AttributeName=turn_id,AttributeType=S \
  --key-schema \
    AttributeName=chat_id,KeyType=HASH \
    AttributeName=turn_id,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1
```

### Step 2 — `bedrock_model.py` (replaces `local_model.py`)
- Remove: GGUF file discovery, `ChatLlamaCpp`, `llama.cpp`, `_INFERENCE_LOCK`
- Keep: `answer_question()` signature identical
- Keep: same prompt structure (system + history + context + question)
- Keep: same output dict shape (model_id, display_name, text, citations, sources, usage, performance)
- Replace: `model.invoke(messages)` → `bedrock.invoke_model()`
- Replace: model catalog from local files → hardcoded Bedrock model list

### Step 3 — `lambda_indexer.py` (replaces `local_server.py` index routes + `indexing.py`)
- Keep: same request validation
- Keep: `chunker.py` unchanged (import directly)
- Keep: `ocr_source.py` unchanged (import directly)
- Replace: `vector_store.replace_document_chunks()` → DynamoDB batch write
- Add: async job pattern (same as existing ingestion Lambda)

### Step 4 — `lambda_chat.py` (replaces `local_server.py` chat routes + `chat_graph.py`)
- Keep: LangGraph flow (retrieve → answer → persist)
- Keep: BM25 search logic from `vector_store.py`
- Replace: DynamoDB reads instead of `chunks.json`
- Replace: `local_model.answer_question()` → `bedrock_model.answer_question()`
- Replace: DynamoDB writes instead of `chats.json`

### Step 5 — `lambda_retrieval_llm.py`
- GET /api/llm/health → return 200
- GET /api/llm/models → return hardcoded Bedrock model list
- GET /api/llm/chats → DynamoDB scan Chats table (META records only)
- GET /api/llm/chats/{chat_id} → DynamoDB query all turns for chat_id

### Step 6 — Deploy
```bash
# Zip each Lambda with its dependencies
# Add API Gateway routes
# Set env vars: CHUNKS_TABLE, CHATS_TABLE, BEDROCK_MODEL_IDs, OCR_API_BASE_URL
```

### Step 7 — Frontend update (`llmApi.js`)
- Change base URL from `http://127.0.0.1:8787` to API Gateway URL
- Change `/api/` prefix to `/api/llm/`
- Model IDs change from GGUF filenames to Bedrock model IDs
- Everything else stays identical

---

## Environment variables for Lambdas

```
OCR_API_BASE_URL          = https://o0dqnexpsg.execute-api.ap-south-1.amazonaws.com/prod
CHUNKS_TABLE              = jrf-task-llm-Chunks
CHATS_TABLE               = jrf-task-llm-Chats
AWS_REGION                = ap-south-1
ALLOWED_ORIGIN            = https://jrf-nlp-pipeline.skillrouteai.com
DEFAULT_TOP_K             = 5
MAX_TOP_K                 = 8
MAX_DOCUMENTS_PER_REQUEST = 3
```

---

## What the frontend needs to change

| Current (local) | After migration |
|---|---|
| `http://127.0.0.1:8787` | API Gateway URL (ap-south-1) |
| `/api/models` | `/api/llm/models` |
| `/api/documents/index` | `/api/llm/documents/index` |
| `/api/chat` | `/api/llm/chat` |
| `/api/chats` | `/api/llm/chats` |
| Model IDs = GGUF filenames | Model IDs = Bedrock model IDs |

LLMTab.jsx, LLMComparePane.jsx, HistoryOfLLMChat.jsx — zero logic changes.  
Only `llmApi.js` base URL and model ID constants need updating.

---

## Cost estimate (research scale)

| Service | Usage | Monthly cost |
|---|---|---|
| Nova Lite (200K tokens/month) | ~500 questions | ~$0.06 |
| Nova Pro (200K tokens/month) | ~200 questions | ~$0.80 |
| DynamoDB (Chunks + Chats) | PAY_PER_REQUEST | ~$0.10 |
| Lambda (3 functions) | ~1000 invocations | Free tier |
| API Gateway | ~1000 requests | Free tier |
| **Total** | | **~$1/month** |

vs local: electricity + CPU time + 30-120s model load per question.

---

## Notes

- Do Sydney → Mumbai migration (WP1 NLP pipeline) FIRST before this migration
- OCR API URL in env vars must be updated to ap-south-1 endpoint after that migration
- Bedrock Nova Pro availability in ap-south-1 must be verified before Step 2
- The SCP policy blocked ap-south-1 DynamoDB access — verify this is resolved before starting
- `chunker.py` and `ocr_source.py` are copied unchanged into each Lambda zip
- No vector embeddings in this plan — BM25 lexical search is kept as-is
- Semantic/embedding search (OpenSearch, pgvector) is a future enhancement, not in scope here
