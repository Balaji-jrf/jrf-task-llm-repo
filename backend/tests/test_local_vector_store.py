import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jrf_task_llm import vector_store


def _chunk(chunk_id, document_id, text):
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "filename": f"{document_id}.pdf",
        "page": 1,
        "text": text,
        "bbox": [0.1, 0.2, 0.8, 0.9],
    }


class LocalVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.vector_file = Path(self.temporary_directory.name) / "vectors.json"
        self.file_path = patch.object(vector_store, "LOCAL_CHUNKS_FILE", self.vector_file)
        self.file_path.start()
        self.addCleanup(self.temporary_directory.cleanup)
        self.addCleanup(self.file_path.stop)

    def test_search_ranks_similar_chunks_and_filters_selected_documents(self):
        vector_store.replace_document_chunks("doc-a", [
            _chunk("a-match", "doc-a", "Heat wave preparedness plans guide health services."),
            _chunk("a-other", "doc-a", "Community clinics provide routine care."),
        ])
        vector_store.replace_document_chunks("doc-b", [
            _chunk("b-match", "doc-b", "Heat wave emergency response in other report."),
        ])

        results = vector_store.search_chunks("heat wave preparedness", ["doc-a"], 2)

        self.assertEqual([item["chunk_id"] for item in results], ["a-match"])
        self.assertGreater(results[0]["score"], 0)
        self.assertEqual(results[0]["bbox"], [0.1, 0.2, 0.8, 0.9])

    def test_replacing_document_chunks_removes_old_entries(self):
        vector_store.replace_document_chunks("doc-a", [
            _chunk("old", "doc-a", "old passage"),
        ])
        vector_store.replace_document_chunks("doc-a", [
            _chunk("new", "doc-a", "new passage"),
        ])

        results = vector_store.search_chunks("new passage", ["doc-a"], 5)

        self.assertEqual([item["chunk_id"] for item in results], ["new"])

    def test_query_with_no_terms_returns_no_chunks(self):
        vector_store.replace_document_chunks("doc-a", [
            _chunk("a", "doc-a", "A source passage."),
        ])

        self.assertEqual(vector_store.search_chunks("!!!", ["doc-a"], 5), [])


if __name__ == "__main__":
    unittest.main()
