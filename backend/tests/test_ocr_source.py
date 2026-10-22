import json
import unittest
from io import BytesIO
from unittest.mock import patch

from jrf_task_llm import ocr_source


class OcrSourceTests(unittest.TestCase):
    def test_document_id_is_escaped_as_a_single_path_segment(self):
        responses = [
            BytesIO(json.dumps({
                "status": "completed",
                "ocr_presigned_url": "https://example.test/ocr.json",
                "filename": "report.pdf",
            }).encode()),
            BytesIO(json.dumps({"elements": []}).encode()),
        ]
        with (
            patch.object(ocr_source, "OCR_API_BASE_URL", "https://api.test/"),
            patch.object(ocr_source, "urlopen", side_effect=responses) as urlopen,
        ):
            document = ocr_source.fetch_ocr_document("doc/with space")

        self.assertEqual(document["filename"], "report.pdf")
        self.assertEqual(
            urlopen.call_args_list[0].args[0],
            "https://api.test/document/doc%2Fwith%20space",
        )


if __name__ == "__main__":
    unittest.main()
