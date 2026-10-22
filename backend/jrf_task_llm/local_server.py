import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from . import chat_graph, chat_store, indexing, local_model
from .config import (
    ALLOWED_ORIGINS,
    LOCAL_API_HOST,
    LOCAL_API_PORT,
    LOCAL_DATA_DIR,
    MAX_DOCUMENTS_PER_INDEX_REQUEST,
    MAX_TOP_K,
    DEFAULT_TOP_K,
    LOCAL_CHAT_MODEL_PATH,
)

LOGGER = logging.getLogger(__name__)
_JOBS = {}
_JOBS_LOCK = Lock()


class RequestError(ValueError):
    pass


def _document_ids(value) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RequestError("document_ids must be a non-empty array.")
    if len(value) > MAX_DOCUMENTS_PER_INDEX_REQUEST:
        raise RequestError(
            f"Select no more than {MAX_DOCUMENTS_PER_INDEX_REQUEST} documents."
        )
    if any(not isinstance(item, str) for item in value):
        raise RequestError("document_ids must contain strings.")
    document_ids = [item.strip() for item in value]
    if (
        any(not item or len(item) > 128 for item in document_ids)
        or len(set(document_ids)) != len(document_ids)
    ):
        raise RequestError("document_ids must contain unique, non-empty IDs.")
    return document_ids


class LocalApiHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin", "") not in ALLOWED_ORIGINS:
            self._send_json(403, {"detail": "Origin is not allowed."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and origin not in ALLOWED_ORIGINS:
            self._send_json(403, {"detail": "Origin is not allowed."})
            return

        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if self.command == "GET":
                self._get(path)
            elif self.command == "POST":
                self._post(path, self._read_body())
            else:
                self._send_json(405, {"detail": "Method is not allowed."})
        except RequestError as exc:
            self._send_json(400, {"detail": str(exc)})
        except KeyError as exc:
            self._send_json(404, {"detail": str(exc)})
        except Exception as exc:
            LOGGER.exception("Local LLM API request failed: method=%s path=%s", self.command, path)
            self._send_json(500, {"detail": f"Local LLM request failed: {str(exc)[:400]}"})

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError("Invalid content length.") from exc
        if length < 0 or length > 65536:
            raise RequestError("Request body must be between 0 and 65536 bytes.")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RequestError("Request body must be a JSON object.")
        return payload

    def _get(self, path: str) -> None:
        if path == "/api/health":
            self._send_json(200, {"status": "ok"})
            return
        if path == "/api/models":
            self._send_json(200, {"models": local_model.available_models()})
            return
        if path == "/api/chats":
            self._send_json(200, {"chats": chat_store.list_chats()})
            return
        match = re.fullmatch(r"/api/chats/([A-Za-z0-9-]{1,128})", path)
        if match:
            self._send_json(200, chat_store.get_chat(match.group(1)))
            return
        match = re.fullmatch(r"/api/documents/index/([A-Za-z0-9-]{1,128})", path)
        if match:
            self._get_job(match.group(1))
            return
        match = re.fullmatch(r"/api/chat-jobs/([A-Za-z0-9-]{1,128})", path)
        if match:
            self._get_job(match.group(1))
            return
        self._send_json(404, {"detail": "Route not found."})

    def _get_job(self, job_id: str) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        self._send_json(200, job)

    def _post(self, path: str, payload: dict) -> None:
        if path == "/api/documents/index":
            self._index_documents(payload)
            return
        if path == "/api/chat":
            self._start_chat(payload)
            return
        self._send_json(404, {"detail": "Route not found."})

    def _index_documents(self, payload: dict) -> None:
        document_ids = _document_ids(payload.get("document_ids"))
        job_id = str(uuid4())
        try:
            indexed = [indexing.index_document(document_id) for document_id in document_ids]
            job = {
                "job_id": job_id,
                "status": "completed",
                "indexed_documents": indexed,
            }
        except Exception as exc:
            LOGGER.exception("Local document indexing failed: job_id=%s", job_id)
            job = {
                "job_id": job_id,
                "status": "failed",
                "error": str(exc)[:500],
            }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
        self._send_json(202, {"job_id": job_id, "status": "processing"})

    def _start_chat(self, payload: dict) -> None:
        question_value = payload.get("question", "")
        if not isinstance(question_value, str):
            raise RequestError("question must be a string.")
        question = question_value.strip()
        if not question or len(question) > 4000:
            raise RequestError("question must contain 1-4000 characters.")
        document_ids = _document_ids(payload.get("document_ids"))
        try:
            model_ids = local_model.validate_models(payload.get("model_ids"))
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        try:
            top_k = min(MAX_TOP_K, max(1, int(payload.get("top_k", DEFAULT_TOP_K))))
        except (TypeError, ValueError) as exc:
            raise RequestError("top_k must be an integer.") from exc

        chat_id = str(payload.get("chat_id") or "").strip()
        if chat_id:
            try:
                chat_store.get_chat(chat_id)
            except KeyError as exc:
                raise RequestError("chat_id does not identify an existing chat.") from exc
        else:
            chat_id, _ = chat_store.create_chat(document_ids, model_ids, question)

        job_id = str(uuid4())
        try:
            result = chat_graph.run_chat(chat_id, {
                "question": question,
                "document_ids": document_ids,
                "model_ids": model_ids,
                "top_k": top_k,
            })
            job = {
                "job_id": job_id,
                "chat_id": chat_id,
                "status": "completed",
                "result": result,
            }
        except Exception as exc:
            LOGGER.exception("Local chat request failed: job_id=%s", job_id)
            job = {
                "job_id": job_id,
                "chat_id": chat_id,
                "status": "failed",
                "error": str(exc)[:500],
            }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
        self._send_json(202, {
            "job_id": job_id,
            "chat_id": chat_id,
            "status": "processing",
        })


class LocalApiServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    model_path = Path(LOCAL_CHAT_MODEL_PATH).expanduser() if LOCAL_CHAT_MODEL_PATH else None
    if model_path is None or not model_path.is_file():
        raise SystemExit(
            "Set LOCAL_CHAT_MODEL_PATH to the downloaded Hugging Face GGUF model file."
        )
    data_directory = Path(LOCAL_DATA_DIR).expanduser()
    data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = LocalApiServer((LOCAL_API_HOST, LOCAL_API_PORT), LocalApiHandler)
    print(f"Local LLM API listening at http://{LOCAL_API_HOST}:{LOCAL_API_PORT}/api")
    print(f"Local vectors and chat history: {data_directory}")
    server.serve_forever()


if __name__ == "__main__":
    main()
