# Local run commands

These steps run the React frontend and local GGUF models. The backend reads OCR
documents through the existing OCR API, so that service must be reachable. No cloud
deployment or credentials are needed for local model inference.

Run backend and frontend in **two separate terminals**. Start the backend first.

## First-time setup

Open Terminal 1 at the repository root:

```bash
cd "/home/balaji/Documents/Balaji-JRF Works/WP1-target1-BREATHE/jrf-task-llm/backend"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

The model files are stored outside the repository in:

```text
~/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/
├── qwen2.5-1.5b-instruct-q4_k_m.gguf
├── Qwen2.5-7B-Instruct-Q4_K_M.gguf
├── DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf
└── Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

If the `hf` command is not found, activate the backend virtual environment and
install the backend requirements again; `huggingface-hub` provides the CLI.
The backend discovers `.gguf` files directly in this folder when it starts.
Incomplete files and files in nested subdirectories will not be listed.

Install frontend dependencies once in Terminal 2:

```bash
cd "/home/balaji/Documents/Balaji-JRF Works/WP1-target1-BREATHE/jrf-task-llm/frontend"
npm ci
```

## Start the backend (Terminal 1, each run)

```bash
cd "/home/balaji/Documents/Balaji-JRF Works/WP1-target1-BREATHE/jrf-task-llm/backend"
source .venv/bin/activate

# 1. Folder containing your GGUF models
export LOCAL_MODEL_DIR="$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace"

# 2. Specific GGUF model file to load
export LOCAL_CHAT_MODEL_PATH="$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/Qwen2.5-7B-Instruct-Q4_K_M.gguf"

# 3. Server Configuration
export OCR_API_BASE_URL="https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod"
export JRF_TASK_LLM_DATA_DIR="$HOME/.local/share/jrf-task-llm"
export JRF_TASK_LLM_HOST="127.0.0.1"
export JRF_TASK_LLM_PORT="8787"
export JRF_TASK_LLM_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"

python -m jrf_task_llm.local_server


TO switch Models
1. Ctrl + C
2. export LOCAL_CHAT_MODEL_PATH="..."
3. python -m jrf_task_llm.local_server

To switch to Llama 3.1 8B:
export LOCAL_CHAT_MODEL_PATH="$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
python -m jrf_task_llm.local_server

To switch to DeepSeek-R1 Distill 7B:
export LOCAL_CHAT_MODEL_PATH="$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
python -m jrf_task_llm.local_server

To switch back to Qwen 2.5 7B:
export LOCAL_CHAT_MODEL_PATH="$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
python -m jrf_task_llm.local_server
```

Leave this terminal open. The API listens at `http://127.0.0.1:8787/api`. Models
are loaded lazily when selected. The backend retains only one loaded model at a
time; switching models closes the previous one before loading the next.

## Start the frontend (Terminal 2, each run)

```bash
cd "/home/balaji/Documents/Balaji-JRF Works/WP1-target1-BREATHE/jrf-task-llm/frontend"

VITE_JRF_TASK_LLM_API_URL="http://127.0.0.1:8787/api" \
VITE_OCR_API_URL="https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod" \
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Keep this terminal open while
using the application.

## Check the local backend

Use a third terminal:

```bash
curl --fail --show-error http://127.0.0.1:8787/api/health
curl --fail --show-error http://127.0.0.1:8787/api/models
curl --fail --show-error http://127.0.0.1:8787/api/chats
```

Expected health response:

```json
{"status": "ok"}
```

The model endpoint should list each discovered model's tag, file name, and size.
The UI lets you select up to four models. Multiple selected models receive the same
question and retrieved OCR passages; responses appear in separate, tagged cards
with model-load time, inference time, and output-token speed when available.

## Try a document and question from the API

The UI normally performs these steps. The commands below let you check them without
the browser.

First list documents from the OCR service and copy a completed `document_id`:

```bash
curl --fail --show-error \
  "https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod/documents"
```

Set `DOCUMENT_ID` to one of the completed document IDs from that response, then
request indexing:

```bash
export DOCUMENT_ID="paste-a-completed-document-id-here"

curl --fail --show-error \
  -X POST http://127.0.0.1:8787/api/documents/index \
  -H 'Content-Type: application/json' \
  -d "{\"document_ids\":[\"$DOCUMENT_ID\"]}"
```

The response includes a `job_id`. Use it to read the indexing result; the result
should say `completed` and report `chunks_indexed`:

```bash
export INDEX_JOB_ID="paste-the-returned-job-id-here"
curl --fail --show-error \
  "http://127.0.0.1:8787/api/documents/index/$INDEX_JOB_ID"
```

Ask a question using the configured model:

```bash
curl --fail --show-error \
  -X POST http://127.0.0.1:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d "{\"question\":\"What are the key recommendations?\",\"document_ids\":[\"$DOCUMENT_ID\"],\"model_ids\":[\"Qwen2.5-1.5B-Instruct-Q4_K_M\"]}"
```

For a model comparison, send multiple IDs in `model_ids`. Use the IDs exactly as
returned by `GET /api/models`:

```bash
curl --fail --show-error \
  -X POST http://127.0.0.1:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d "{\"question\":\"What are the key recommendations?\",\"document_ids\":[\"$DOCUMENT_ID\"],\"model_ids\":[\"Qwen2.5-1.5B-Instruct-Q4_K_M\",\"Qwen2.5-7B-Instruct-Q4_K_M\",\"DeepSeek-R1-Distill-Qwen-7B-Q4_K_M\",\"Meta-Llama-3.1-8B-Instruct-Q4_K_M\"]}"
```

The API runs these models sequentially to limit RAM use. Each answer (or individual
model error) is stored with its model ID inside the same conversation turn.

Use the returned `job_id` to retrieve the answer and citations:

```bash
export CHAT_JOB_ID="paste-the-returned-job-id-here"
curl --fail --show-error \
  "http://127.0.0.1:8787/api/chat-jobs/$CHAT_JOB_ID"
```

Open saved chats with:

```bash
curl --fail --show-error http://127.0.0.1:8787/api/chats
```

## Local files and persistence

The local models folder is:

```text
~/Documents/Balaji-JRF Works/LLM(models) from HuggingFace/
```

The base Qwen model was moved there from `~/models/jrf-task-llm/`. The backend
uses `LOCAL_MODEL_DIR` to discover each `.gguf` file in this folder. Set
`LOCAL_MODEL_DIR` in Terminal 1 if you store the models elsewhere.

The default data directory is:

```text
~/.local/share/jrf-task-llm/
├── chunks.json
└── chats.json
```

Indexed chunks and chat history persist after stopping/restarting the backend.
The job lookup records live only in the backend process and are lost when it stops.
Do not delete the data directory if you want to keep indexed chunks and chats.

## Stop and restart

Press `Ctrl+C` in each terminal to stop its process. To run again, repeat the
backend and frontend start sections; dependency and model downloads are only
needed for initial setup. The two environment configurations are terminal-local,
so re-run their `export` commands when starting a fresh backend terminal.

## Troubleshooting

### No models appear or a selected model cannot be loaded

Confirm `LOCAL_MODEL_DIR` points at the folder containing complete GGUF files:

```bash
ls -lh "$HOME/Documents/Balaji-JRF Works/LLM(models) from HuggingFace"/*.gguf
```

Restart the backend after adding/removing a model. If one selected model fails to
load, the other selected models can still return answers, and the failing model is
shown with its own error card.

### Port 8787 or 3000 is already in use

Stop the old backend/frontend with `Ctrl+C` in the terminal where it is running,
then start it again. The backend port can be changed with
`JRF_TASK_LLM_PORT`; the frontend development port is configured in
`frontend/package.json`.

### OCR documents do not appear in the frontend

Check that the OCR API is reachable:

```bash
curl --fail --show-error \
  "https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod/documents"
```

The browser calls this OCR API directly, so its CORS policy must allow
`http://localhost:3000`. If CORS is missing, local React and Qwen can still run,
but the browser cannot load the OCR list or previews until the OCR service allows
that origin.

### Backend health works but chat fails

Index a completed document before asking questions. Inspect the backend terminal
for the request error. The first answer for each selected model includes its model
load time. CPU comparisons run sequentially and may take several minutes per
model; avoid interrupting the backend while a comparison is running.

## Run backend tests

```bash
cd "/home/balaji/Documents/Balaji-JRF Works/WP1-target1-BREATHE/jrf-task-llm/backend"
source .venv/bin/activate
PYTHONPATH=. python -m unittest discover -s tests -v
python -m compileall -q jrf_task_llm tests
```
