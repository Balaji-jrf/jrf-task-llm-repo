import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from jrf_task_llm import local_server
from jrf_task_llm.config import LOCAL_CHAT_MODEL_ID


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        local_server._JOBS.clear()
        self.server = local_server.LocalApiServer(("127.0.0.1", 0), local_server.LocalApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)

    def _request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Origin": "http://localhost:3000",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, json.load(response)

    def test_models_endpoint_returns_allow_list_with_local_cors(self):
        available = [
            {"model_id": LOCAL_CHAT_MODEL_ID, "display_name": "Local Qwen", "enabled": True},
            {"model_id": "Qwen2.5-7B-Instruct-Q4_K_M", "display_name": "Qwen 7B", "enabled": True},
        ]
        with patch.object(local_server.local_model, "available_models", return_value=available):
            status, headers, body = self._request("/api/models")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertEqual(body["models"], available)

    def test_health_endpoint_returns_ok(self):
        status, _, body = self._request("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_chat_rejects_non_local_model(self):
        request = Request(
            self.base_url + "/api/chat",
            data=json.dumps({
                "question": "Question",
                "document_ids": ["doc-a"],
                "model_ids": ["another-model"],
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 400)

    def test_indexing_route_runs_and_exposes_completed_job(self):
        indexed_document = {
            "document_id": "doc-a",
            "filename": "report.pdf",
            "chunks_indexed": 3,
        }
        with patch.object(local_server.indexing, "index_document", return_value=indexed_document):
            status, _, response = self._request(
                "/api/documents/index",
                method="POST",
                payload={"document_ids": ["doc-a"]},
            )
        self.assertEqual(status, 202)

        status, _, job = self._request(f"/api/documents/index/{response['job_id']}")

        self.assertEqual(status, 200)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["indexed_documents"], [indexed_document])

    def test_indexing_rejects_empty_document_selection(self):
        request = Request(
            self.base_url + "/api/documents/index",
            data=b'{"document_ids":[]}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
