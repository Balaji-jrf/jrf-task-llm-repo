import unittest

from jrf_task_llm.chunker import chunk_document


class ChunkDocumentTests(unittest.TestCase):
    def test_chunks_by_page_and_retains_bbox_citations(self):
        document = {
            "document_id": "doc-123",
            "filename": "report.pdf",
            "elements": [
                {"page": 2, "text": "second page", "bbox": [0.1, 0.2, 0.3, 0.4]},
                {"page": 1, "text": "first", "bbox": [0.2, 0.1, 0.4, 0.3]},
                {"page": 1, "text": "page text", "bbox": [0.1, 0.4, 0.5, 0.8]},
            ],
        }

        chunks = chunk_document(document)

        self.assertEqual([chunk["page"] for chunk in chunks], [1, 2])
        self.assertEqual(chunks[0]["text"], "first page text")
        self.assertEqual(chunks[0]["bbox"], [0.1, 0.1, 0.5, 0.8])
        self.assertEqual(chunks[0]["filename"], "report.pdf")

    def test_splits_long_page_text_and_uses_stable_ids(self):
        document = {
            "document_id": "doc-123",
            "elements": [
                {"page": 1, "text": "a" * 8},
                {"page": 1, "text": "b" * 8},
                {"page": 1, "text": "c" * 8},
            ],
        }

        first_run = chunk_document(document, max_chars=17)
        second_run = chunk_document(document, max_chars=17)

        self.assertEqual(len(first_run), 3)
        self.assertEqual(
            [chunk["chunk_id"] for chunk in first_run],
            [chunk["chunk_id"] for chunk in second_run],
        )


if __name__ == "__main__":
    unittest.main()
