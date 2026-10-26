import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from jrf_task_llm import local_model
from jrf_task_llm.config import LOCAL_CHAT_MODEL_ID


class LocalModelTests(unittest.TestCase):
    def test_discovers_gguf_models_and_validates_multiple_selections(self):
        with TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "qwen2.5-1.5b-instruct-q4_k_m.gguf").write_bytes(b"qwen")
            (model_dir / "Qwen2.5-7B-Instruct-Q4_K_M.gguf").write_bytes(b"qwen7")
            (model_dir / "notes.txt").write_text("ignored")
            with patch.object(local_model, "LOCAL_MODEL_DIR", model_dir):
                available = local_model.available_models()
                self.assertEqual(
                    [model["model_id"] for model in available],
                    [LOCAL_CHAT_MODEL_ID, "Qwen2.5-7B-Instruct-Q4_K_M"],
                )
                self.assertEqual(available[0]["display_name"], "qwen2.5 1.5b instruct q4 k m")
                self.assertEqual(
                    local_model.validate_models([
                        LOCAL_CHAT_MODEL_ID,
                        "Qwen2.5-7B-Instruct-Q4_K_M",
                    ]),
                    [LOCAL_CHAT_MODEL_ID, "Qwen2.5-7B-Instruct-Q4_K_M"],
                )
                with self.assertRaisesRegex(ValueError, "not available"):
                    local_model.validate_models(["missing-model"])

    def test_model_selection_rejects_empty_duplicate_and_oversized_lists(self):
        with patch.object(local_model, "_model_catalog", return_value={
            LOCAL_CHAT_MODEL_ID: {"model_id": LOCAL_CHAT_MODEL_ID},
        }):
            for model_ids in ([], [LOCAL_CHAT_MODEL_ID, LOCAL_CHAT_MODEL_ID], ["m"] * 5):
                with self.subTest(model_ids=model_ids), self.assertRaises(ValueError):
                    local_model.validate_models(model_ids)

    def test_answer_includes_only_model_cited_sources_and_no_cloud_cost(self):
        chunks = [
            {
                "document_id": "doc-1",
                "filename": "report.pdf",
                "page": 3,
                "bbox": [0.1, 0.2, 0.8, 0.9],
                "text": "The plan protects high-risk groups.",
                "score": 1.25,
            },
            {
                "document_id": "doc-1",
                "filename": "report.pdf",
                "page": 4,
                "bbox": None,
                "text": "Additional source passage.",
                "score": 0.5,
            },
        ]
        response = SimpleNamespace(
            content="Protect high-risk groups [S1].",
            usage_metadata={"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
            response_metadata={},
        )
        model = SimpleNamespace(invoke=lambda messages: response)
        with (
            patch.object(local_model, "_load_model", return_value=(model, 1.25)),
            patch.object(local_model.time, "perf_counter", side_effect=[10.0, 11.0]),
        ):
            answer = local_model.answer_question(
                LOCAL_CHAT_MODEL_ID,
                "What is the goal?",
                chunks,
                [],
            )

        self.assertEqual(answer["model_id"], LOCAL_CHAT_MODEL_ID)
        self.assertEqual(answer["citations"][0]["page"], 3)
        self.assertEqual([source["source_id"] for source in answer["sources"]], ["S1", "S2"])
        self.assertEqual(answer["usage"]["total_tokens"], 26)
        self.assertEqual(answer["performance"]["model_load_seconds"], 1.25)
        self.assertGreater(answer["performance"]["inference_seconds"], 0)
        self.assertGreater(answer["performance"]["output_tokens_per_second"], 0)
        self.assertNotIn("estimated_model_cost_usd", answer)


if __name__ == "__main__":
    unittest.main()
