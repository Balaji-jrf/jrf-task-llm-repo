import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jrf_task_llm import local_model
from jrf_task_llm.config import LOCAL_CHAT_MODEL_ID


class LocalModelTests(unittest.TestCase):
    def test_only_configured_qwen_model_is_available(self):
        self.assertEqual(
            local_model.available_models(),
            [{"model_id": LOCAL_CHAT_MODEL_ID, "enabled": True}],
        )
        self.assertEqual(
            local_model.validate_models([LOCAL_CHAT_MODEL_ID]),
            [LOCAL_CHAT_MODEL_ID],
        )
        with self.assertRaisesRegex(ValueError, "configured local model"):
            local_model.validate_models(["another-model"])

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
        with patch.object(local_model, "_chat_model", return_value=model):
            answer = local_model.answer_question("What is the goal?", chunks, [])

        self.assertEqual(answer["model_id"], LOCAL_CHAT_MODEL_ID)
        self.assertEqual(answer["citations"][0]["page"], 3)
        self.assertEqual([source["source_id"] for source in answer["sources"]], ["S1", "S2"])
        self.assertEqual(answer["usage"]["total_tokens"], 26)
        self.assertNotIn("estimated_model_cost_usd", answer)


if __name__ == "__main__":
    unittest.main()
