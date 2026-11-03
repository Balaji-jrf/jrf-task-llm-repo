import unittest

from chunker import chunk_document


class ChunkerTests(unittest.TestCase):
    def test_chunks_are_page_local_and_keep_bounding_boxes(self):
        ocr = {
            "document_id": "doc-1",
            "filename": "history.pdf",
            "elements": [
                {"page": 1, "text": "Rainfall was recorded in Bidar.", "bbox": [0.1, 0.1, 0.8, 0.2]},
                {"page": 3, "text": "The harvest improved.", "bbox": [0.2, 0.3, 0.7, 0.4]},
            ],
            "tables": [
                {
                    "page": 1,
                    "bbox": [0.1, 0.4, 0.9, 0.8],
                    "rows": [["Year", "Rainfall"], ["1934", "500 mm"]],
                },
            ],
        }

        chunks = chunk_document(ocr, source_hash="source", max_chars=70)

        self.assertTrue(chunks)
        self.assertTrue(all(chunk["page_start"] == chunk["page_end"] == chunk["page"] for chunk in chunks))
        self.assertTrue(all(len(chunk["text"]) <= 70 for chunk in chunks))
        self.assertIn("Year | Rainfall", "\n".join(chunk["text"] for chunk in chunks))
        self.assertTrue(any(chunk["content_type"] == "table" for chunk in chunks))
        self.assertTrue(all(chunk["source_hash"] == "source" for chunk in chunks))
        self.assertTrue(all(chunk["bbox"] for chunk in chunks))

    def test_long_lines_split_without_losing_text(self):
        sentence = "one two three four five six seven eight nine ten"
        chunks = chunk_document(
            {"document_id": "doc-2", "elements": [{"page": 7, "text": sentence}]},
            max_chars=12,
        )

        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunk["text"] for chunk in chunks), sentence)
        self.assertEqual({7}, {chunk["page"] for chunk in chunks})

    def test_requires_document_id(self):
        with self.assertRaisesRegex(ValueError, "document_id"):
            chunk_document({"elements": []})

    def test_empty_ocr_produces_no_chunks(self):
        self.assertEqual(
            [],
            chunk_document({"document_id": "doc-empty", "elements": [], "tables": []}),
        )


if __name__ == "__main__":
    unittest.main()
