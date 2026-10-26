import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jrf_task_llm import chat_store
from jrf_task_llm.config import LOCAL_CHAT_MODEL_ID


class LocalChatStoreTests(unittest.TestCase):
    def test_persists_chat_turns_and_recent_history(self):
        with TemporaryDirectory() as directory:
            with (
                patch.object(chat_store, "LOCAL_CHAT_FILE", Path(directory) / "chats.json"),
            ):
                chat_id, created_at = chat_store.create_chat(
                    ["doc-a"],
                    [LOCAL_CHAT_MODEL_ID],
                    "What is the plan?",
                )
                turn = {
                    "question": "What is the plan?",
                    "document_ids": ["doc-a"],
                    "model_ids": [LOCAL_CHAT_MODEL_ID],
                    "answers": [{"model_id": LOCAL_CHAT_MODEL_ID, "text": "See [S1]."}],
                    "retrieved_sources": [
                        {"page": 1, "bbox": [0.1, 0.2, 0.8, 0.9], "score": 1.25},
                    ],
                }
                chat_store.append_turn(chat_id, turn)
                second_model_id = "Qwen2.5-7B-Instruct-Q4_K_M"
                chat_store.append_turn(chat_id, {
                    **turn,
                    "model_ids": [second_model_id],
                    "answers": [{"model_id": second_model_id, "text": "Another answer [S1]."}],
                })

                record = chat_store.get_chat(chat_id)
                history = chat_store.recent_history(chat_id)
                summaries = chat_store.list_chats()

        self.assertTrue(created_at)
        self.assertEqual(record["chat"]["chat_id"], chat_id)
        self.assertEqual(record["turns"][0]["question"], turn["question"])
        self.assertEqual(history[0]["answers"][0]["text"], "See [S1].")
        self.assertEqual(
            record["turns"][0]["retrieved_sources"][0]["bbox"],
            turn["retrieved_sources"][0]["bbox"],
        )
        self.assertEqual(record["turns"][0]["retrieved_sources"][0]["score"], 1.25)
        self.assertEqual(summaries[0]["turn_count"], 2)
        self.assertEqual(
            record["chat"]["model_ids"],
            [LOCAL_CHAT_MODEL_ID, second_model_id],
        )


if __name__ == "__main__":
    unittest.main()
