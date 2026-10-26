import os
from pathlib import Path


OCR_API_BASE_URL = os.environ.get("OCR_API_BASE_URL", "").rstrip("/")
LOCAL_DATA_DIR = os.environ.get(
    "JRF_TASK_LLM_DATA_DIR",
    str(Path.home() / ".local" / "share" / "jrf-task-llm"),
)
LOCAL_MODEL_DIR = Path(os.environ.get(
    "LOCAL_MODEL_DIR",
    str(Path.home() / "Documents" / "Balaji-JRF Works" / "LLM(models) from HuggingFace"),
)).expanduser()
LOCAL_CHAT_MODEL_PATH = os.environ.get("LOCAL_CHAT_MODEL_PATH", "").strip()
LOCAL_CHAT_MODEL_ID = "Qwen2.5-1.5B-Instruct-Q4_K_M"
LOCAL_MODEL_THREADS = int(os.environ.get("JRF_TASK_LLM_MODEL_THREADS", "8"))
LOCAL_MODEL_CONTEXT = int(os.environ.get("JRF_TASK_LLM_MODEL_CONTEXT", "8192"))
LOCAL_MODEL_MAX_TOKENS = int(os.environ.get("JRF_TASK_LLM_MODEL_MAX_TOKENS", "700"))
LOCAL_API_HOST = os.environ.get("JRF_TASK_LLM_HOST", "127.0.0.1")
LOCAL_API_PORT = int(os.environ.get("JRF_TASK_LLM_PORT", "8787"))
_DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
ALLOWED_ORIGINS = frozenset(
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "JRF_TASK_LLM_ALLOWED_ORIGINS",
        _DEFAULT_ALLOWED_ORIGINS,
    ).split(",")
    if origin.strip()
)
MAX_DOCUMENTS_PER_INDEX_REQUEST = 3
MAX_TOP_K = 8
DEFAULT_TOP_K = 5
