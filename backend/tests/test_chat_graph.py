import unittest
from unittest.mock import patch

from jrf_task_llm import chat_graph
from jrf_task_llm.config import LOCAL_CHAT_MODEL_ID


class ChatGraphTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [{
            "document_id": "doc-1",
            "filename": "report.pdf",
            "page": 2,
            "bbox": [0.1, 0.2, 0.8, 0.4],
            "text": "Source text",
            "score": 0.9,
        }]
        self.answer = {
            "model_id": LOCAL_CHAT_MODEL_ID,
            "text": "Answer [S1]",
            "citations": [],
            "sources": self.chunks,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    def test_local_model_uses_text_retrieval_and_persists_citations(self):
        with (
            patch.object(chat_graph.chat_store, "recent_history", return_value=[]) as history,
            patch.object(chat_graph.chat_store, "append_turn") as persist,
            patch.object(chat_graph.vector_store, "search_chunks", return_value=self.chunks) as search,
            patch.object(chat_graph.local_model, "answer_question", return_value=self.answer) as invoke,
        ):
            result = chat_graph.run_chat("chat-local", {
                "question": "What is the plan?",
                "document_ids": ["doc-1"],
                "model_ids": [LOCAL_CHAT_MODEL_ID],
            })

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["answers"], [self.answer])
        history.assert_called_once_with("chat-local")
        search.assert_called_once_with("What is the plan?", ["doc-1"], 5)
        invoke.assert_called_once_with(
            LOCAL_CHAT_MODEL_ID,
            "What is the plan?",
            self.chunks,
            [],
        )
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[1]["documents_fetched"], ["doc-1"])

    def test_multiple_models_are_run_and_saved_with_individual_results(self):
        second_model_id = "Qwen2.5-7B-Instruct-Q4_K_M"
        second_answer = {
            **self.answer,
            "model_id": second_model_id,
            "text": "Second model answer [S1]",
        }
        with (
            patch.object(chat_graph.chat_store, "recent_history", return_value=[]),
            patch.object(chat_graph.chat_store, "append_turn") as persist,
            patch.object(chat_graph.vector_store, "search_chunks", return_value=self.chunks),
            patch.object(
                chat_graph.local_model,
                "answer_question",
                side_effect=[self.answer, second_answer],
            ) as invoke,
        ):
            result = chat_graph.run_chat("chat-local", {
                "question": "What is the plan?",
                "document_ids": ["doc-1"],
                "model_ids": [LOCAL_CHAT_MODEL_ID, second_model_id],
            })

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            [answer["model_id"] for answer in result["answers"]],
            [LOCAL_CHAT_MODEL_ID, second_model_id],
        )
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(persist.call_args.args[1]["model_ids"], [LOCAL_CHAT_MODEL_ID, second_model_id])
        self.assertEqual(persist.call_args.args[1]["answers"], result["answers"])

    def test_one_model_failure_does_not_discard_other_model_answer(self):
        second_model_id = "Qwen2.5-7B-Instruct-Q4_K_M"
        with (
            patch.object(chat_graph.chat_store, "recent_history", return_value=[]),
            patch.object(chat_graph.chat_store, "append_turn") as persist,
            patch.object(chat_graph.vector_store, "search_chunks", return_value=self.chunks),
            patch.object(
                chat_graph.local_model,
                "answer_question",
                side_effect=[self.answer, RuntimeError("load failed")],
            ),
        ):
            result = chat_graph.run_chat("chat-local", {
                "question": "What is the plan?",
                "document_ids": ["doc-1"],
                "model_ids": [LOCAL_CHAT_MODEL_ID, second_model_id],
            })

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["answers"][1]["model_id"], second_model_id)
        self.assertIn("load failed", result["answers"][1]["error"])
        persist.assert_called_once()

    def test_all_model_failures_are_reported_as_failed(self):
        with (
            patch.object(chat_graph.chat_store, "recent_history", return_value=[]),
            patch.object(chat_graph.chat_store, "append_turn") as persist,
            patch.object(chat_graph.vector_store, "search_chunks", return_value=self.chunks),
            patch.object(
                chat_graph.local_model,
                "answer_question",
                side_effect=RuntimeError("load failed"),
            ),
        ):
            result = chat_graph.run_chat("chat-local", {
                "question": "What is the plan?",
                "document_ids": ["doc-1"],
                "model_ids": [LOCAL_CHAT_MODEL_ID],
            })

        self.assertEqual(result["status"], "failed")
        self.assertIn("load failed", result["answers"][0]["error"])
        persist.assert_called_once()


if __name__ == "__main__":
    unittest.main()
