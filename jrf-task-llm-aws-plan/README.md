# jrf-task-llm-aws-plan

AWS serverless implementation of the BREATHE Document AI Chat system.
Replaces the local Python/GGUF stack with Lambda + Bedrock + DynamoDB.

Linked to the NLP Pipeline app — the "Chat with AI" button in Archive Lens opens this app.
Both apps share the same theme (Fraunces + DM Mono + #183a30 / #d46a46 palette).

---

## Folder structure

```
jrf-task-llm-aws-plan/
├── backend/
│   ├── chunker.py              Page-group chunker (10 pages = 1 chunk)
│   ├── bedrock_model.py        Replaces local_model.py — calls Amazon Bedrock
│   ├── lambda_indexer.py       POST /api/llm/documents/index
│   │                           GET  /api/llm/documents/index/{job_id}
│   ├── lambda_chat.py          POST /api/llm/chat
│   │                           GET  /api/llm/chat-jobs/{job_id}
│   └── lambda_retrieval_llm.py GET  /api/llm/health, /models, /chats, /chats/{id}
│                               GET  /api/llm/documents/{id}/chunks
│
└── frontend/
    ├── public/index.html
    ├── src/
    │   ├── index.js
    │   ├── index.css           Same base as NLP pipeline
    │   ├── App.js              Topbar + two tabs (Chat, History)
    │   ├── App.css             Full NLP pipeline theme
    │   ├── llmApi.js           All API calls (/api/llm/* prefix)
    │   └── components/
    │       ├── ChatTab.jsx     Main chat UI + summarization modes
    │       ├── CitationPanel.jsx  Source viewer with bounding box highlight
    │       └── HistoryTab.jsx  Chat history browser
    ├── .env.example
    └── package.json
```

---

## Chunking strategy

One chunk = 10 consecutive pages (configurable via `PAGE_GROUP_SIZE` env var).

- 100-page doc → 10 chunks
- 160-page doc → 16 chunks
- 518-page doc → 52 chunks

This keeps BM25 search fast and gives the LLM a coherent multi-page context window.
The `page_start` / `page_end` fields on each chunk drive the citation panel page navigation.

---

## Summarization modes

| Mode | Button label | What gets sent to the LLM |
|---|---|---|
| `chat` | Q&A Chat | BM25 top-K chunks matching the question |
| `summarize_doc` | Full Doc Summary | All chunks for the document |
| `summarize_chapter` | Chapter-wise | All chunks + chapter hint instruction |
| `summarize_page` | Page Range | Chunks overlapping the given page range |
| `summarize_chunk` | Chunk Range | Specific chunk indices (e.g. 1-3) |

---

## AWS resources needed

### DynamoDB tables (ap-southeast-2, PAY_PER_REQUEST)

```
jrf-task-llm-Chunks
  PK: document_id (S)   SK: chunk_id (S)
  Attrs: filename, page_start, page_end, page, text, bbox, element_count

jrf-task-llm-Chats
  PK: chat_id (S)   SK: turn_id (S)
  turn_id = "META" for header, "TURN#0001" etc for turns

jrf-task-llm-Jobs
  PK: job_id (S)
  TTL: set to 24h to auto-clean
```

### Lambda functions

| Function | Handler | Memory | Timeout | Zip contents |
|---|---|---|---|---|
| `jrf-task-llm-indexer` | `lambda_indexer.lambda_handler` | 512 MB | 120s | `lambda_indexer.py` + `chunker.py` |
| `jrf-task-llm-chat` | `lambda_chat.lambda_handler` | 512 MB | 60s | `lambda_chat.py` + `bedrock_model.py` |
| `jrf-task-llm-retrieval` | `lambda_retrieval_llm.lambda_handler` | 256 MB | 30s | `lambda_retrieval_llm.py` + `bedrock_model.py` |

### Environment variables (all three Lambdas)

```
CHUNKS_TABLE      = jrf-task-llm-Chunks
CHATS_TABLE       = jrf-task-llm-Chats
JOBS_TABLE        = jrf-task-llm-Jobs
OCR_API_BASE_URL  = https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod
ALLOWED_ORIGIN    = https://your-llm-app-domain.com
AWS_REGION        = ap-southeast-2
PAGE_GROUP_SIZE   = 10
DEFAULT_TOP_K     = 5
MAX_TOP_K         = 8
```

### API Gateway routes (add to existing jrf-task-ocr-api)

```
POST /api/llm/chat                          → jrf-task-llm-chat
GET  /api/llm/chat-jobs/{job_id}            → jrf-task-llm-chat
POST /api/llm/documents/index               → jrf-task-llm-indexer
GET  /api/llm/documents/index/{job_id}      → jrf-task-llm-indexer
GET  /api/llm/documents/{document_id}/chunks → jrf-task-llm-retrieval
GET  /api/llm/health                        → jrf-task-llm-retrieval
GET  /api/llm/models                        → jrf-task-llm-retrieval
GET  /api/llm/chats                         → jrf-task-llm-retrieval
GET  /api/llm/chats/{chat_id}               → jrf-task-llm-retrieval
```

---

## Frontend quick start

```bash
cd frontend
cp .env.example .env
# Edit .env — paste your API Gateway URL
npm install
npm start
```

---

## Linking from NLP Pipeline

In the NLP Pipeline's `App.js`, the "Chat with AI" tab should open this app's URL:

```js
// In NLP Pipeline App.js — replace the empty LLM tab with a redirect
{tab === 'llm' && (
  <div style={{ padding: '60px 0', textAlign: 'center' }}>
    <a
      href={process.env.REACT_APP_LLM_APP_URL}
      target="_blank"
      rel="noreferrer"
      className="process-button"
      style={{ display: 'inline-block', textDecoration: 'none' }}
    >
      Open Document AI Chat →
    </a>
  </div>
)}
```

Or open in the same window — both apps share the same theme so the transition is seamless.

---

## IAM permissions needed for Lambda execution role

```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
    "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem",
    "dynamodb:DeleteItem"
  ],
  "Resource": [
    "arn:aws:dynamodb:ap-southeast-2:931092012939:table/jrf-task-llm-*"
  ]
},
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel"],
  "Resource": ["arn:aws:bedrock:ap-southeast-2::foundation-model/*"]
},
{
  "Effect": "Allow",
  "Action": ["lambda:InvokeFunction"],
  "Resource": ["arn:aws:lambda:ap-southeast-2:931092012939:function:jrf-task-llm-*"]
}
```

---

## Build order

1. Create 3 DynamoDB tables (Chunks, Chats, Jobs)
2. Create IAM role with above permissions
3. Zip + deploy `lambda_indexer` (chunker.py included)
4. Zip + deploy `lambda_chat` (bedrock_model.py included)
5. Zip + deploy `lambda_retrieval_llm` (bedrock_model.py included)
6. Add API Gateway routes
7. Set env vars on all 3 Lambdas
8. Deploy frontend (`npm run build` → S3 + CloudFront or Amplify)
9. Update NLP Pipeline `.env` with `REACT_APP_LLM_APP_URL`
