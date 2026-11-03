import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("CHUNKS_TABLE", "test-chunks")
os.environ.setdefault("CHATS_TABLE", "test-chats")
os.environ.setdefault("JOBS_TABLE", "test-jobs")

import bedrock_model
import lambda_chat
import lambda_retrieval_llm


class PagedTable:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        return self.pages[index]


class ChatRetrievalTests(unittest.TestCase):
    def test_chunk_query_reads_every_page_for_each_document(self):
        table = PagedTable([
            {
                "Items": [{"document_id": "doc-1", "chunk_id": "p1"}],
                "LastEvaluatedKey": {"document_id": "doc-1", "chunk_id": "p1"},
            },
            {"Items": [{"document_id": "doc-1", "chunk_id": "p2"}]},
        ])
        with patch.object(lambda_chat, "_chunks_table", table):
            chunks = lambda_chat._get_chunks_for_documents(["doc-1"])
        self.assertEqual(["p1", "p2"], [chunk["chunk_id"] for chunk in chunks])
        self.assertEqual(2, len(table.calls))

    def test_chat_accepts_one_model_unless_compare_is_enabled(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        second_id = bedrock_model.BEDROCK_MODELS[1]["model_id"]
        self.assertTrue(lambda_chat._validate_model_selection([model_id], False))
        self.assertFalse(lambda_chat._validate_model_selection([model_id, second_id], False))
        self.assertTrue(lambda_chat._validate_model_selection([model_id, second_id], True))
        self.assertFalse(lambda_chat._validate_model_selection([model_id, model_id], True))
        self.assertFalse(lambda_chat._validate_model_selection([{}], False))

    def test_bm25_retrieval_represents_multiple_selected_documents(self):
        chunks = [
            {"document_id": "doc-1", "chunk_id": "1", "text": "rainfall records"},
            {"document_id": "doc-1", "chunk_id": "2", "text": "rainfall totals"},
            {"document_id": "doc-2", "chunk_id": "3", "text": "rainfall measurements"},
        ]
        results = lambda_chat._bm25_search("rainfall", chunks, 2)
        self.assertEqual(["doc-1", "doc-2"], [chunk["document_id"] for chunk in results])

    def test_document_chunks_report_persisted_index_status_and_tag(self):
        table = PagedTable([
            {"Items": [{
                "chunk_id": "doc-1-p0001-c001",
                "page": 1,
                "page_start": 1,
                "page_end": 1,
                "content_type": "text",
                "element_count": 3,
                "doc_tag": "Historical Document",
            }]}
        ])
        with patch.object(lambda_retrieval_llm, "_chunks_table", table):
            response = lambda_retrieval_llm.lambda_handler({
                "httpMethod": "GET",
                "path": "/api/llm/documents/doc-1/chunks",
                "pathParameters": {"document_id": "doc-1"},
            }, None)
        body = json.loads(response["body"])
        self.assertEqual("DONE", body["index_status"])
        self.assertEqual(1, body["chunk_count"])
        self.assertEqual("Historical Document", body["doc_tag"])

    def test_document_index_status_uses_count_only_query(self):
        table = PagedTable([{"Count": 13}])
        with patch.object(lambda_retrieval_llm, "_chunks_table", table):
            response = lambda_retrieval_llm.lambda_handler({
                "httpMethod": "GET",
                "path": "/api/llm/documents/doc-1/chunks",
                "pathParameters": {"document_id": "doc-1"},
                "queryStringParameters": {"status_only": "true"},
            }, None)
        body = json.loads(response["body"])
        self.assertEqual("DONE", body["index_status"])
        self.assertEqual(13, body["chunk_count"])
        self.assertEqual("COUNT", table.calls[0]["Select"])
        self.assertNotIn("chunks", body)

    def test_full_document_summary_calls_each_selected_document_separately(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        chunks = [
            {"document_id": "doc-1", "chunk_id": "a", "filename": "one.pdf", "page": 2, "text": "First source"},
            {"document_id": "doc-2", "chunk_id": "b", "filename": "two.pdf", "page": 1, "text": "Second source"},
        ]
        def summarize(**kwargs):
            supplied = kwargs["context_chunks"]
            return {
                "text": f"Finding [{supplied[0]['source_id']}]",
                "citations": [{"source_id": supplied[0]["source_id"], "text": supplied[0]["text"]}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
                "performance": {"inference_seconds": 1.0},
            }
        with patch.object(bedrock_model, "answer_question", side_effect=summarize) as mocked:
                answer = lambda_chat._answer_each_document(
                    model_id, ["doc-1", "doc-2"], chunks, "summarize_doc", {},
                    lambda_chat._build_summarization_question("summarize_doc", {}), [],
                )
        self.assertEqual(2, mocked.call_count)
        self.assertEqual(["one.pdf", "two.pdf"], [
            summary["filename"] for summary in answer["document_summaries"]
        ])
        self.assertEqual(["src1", "src1"], [
            summary["citations"][0]["source_id"] for summary in answer["document_summaries"]
        ])
        self.assertEqual(["doc-1", "doc-2"], [
            call.kwargs["context_chunks"][0]["document_id"] for call in mocked.call_args_list
        ])

    def test_qna_answers_each_selected_document_with_only_its_relevant_chunks(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        chunks = [
            {"document_id": "doc-1", "chunk_id": "a", "filename": "one.pdf", "page": 2, "text": "Heat plan evidence"},
            {"document_id": "doc-2", "chunk_id": "b", "filename": "two.pdf", "page": 4, "text": "Climate health evidence"},
        ]
        def answer(**kwargs):
            source = kwargs["context_chunks"][0]
            return {
                "text": f"Finding from {source['filename']} [src1]",
                "citations": [{"source_id": "src1", "filename": source["filename"]}],
            }
        with patch.object(bedrock_model, "answer_question", side_effect=answer) as mocked:
            result = lambda_chat._answer_each_document(
                model_id, ["doc-1", "doc-2"], chunks, "chat", {},
                "What does this say about heat?", [],
            )
        self.assertEqual(["one.pdf", "two.pdf"], [
            item["filename"] for item in result["document_summaries"]
        ])
        self.assertEqual(["doc-1", "doc-2"], [
            call.kwargs["context_chunks"][0]["document_id"] for call in mocked.call_args_list
        ])
        self.assertIn("one.pdf", mocked.call_args_list[0].kwargs["question"])

    def test_page_and_chapter_summaries_are_scoped_per_document(self):
        chunks = [
            {"document_id": "doc-1", "chunk_id": "a", "filename": "one.pdf", "page": 2, "page_start": 2, "page_end": 2, "text": "Chapter 3: Health context\nOpening chapter text"},
            {"document_id": "doc-1", "chunk_id": "b", "filename": "one.pdf", "page": 9, "page_start": 9, "page_end": 9, "text": "Chapter 4: Vision, Goal and Objectives\nGoal and objectives"},
            {"document_id": "doc-1", "chunk_id": "d", "filename": "one.pdf", "page": 10, "page_start": 10, "page_end": 10, "text": "The vision strengthens healthcare services"},
            {"document_id": "doc-1", "chunk_id": "e", "filename": "one.pdf", "page": 11, "page_start": 11, "page_end": 11, "text": "Chapter 5: Organisational Structure\nThe structure assigns roles"},
            {"document_id": "doc-2", "chunk_id": "c", "filename": "two.pdf", "page": 2, "page_start": 2, "page_end": 2, "text": "Chapter 4: Different chapter\nDifferent chapter"},
        ]
        page_chunks = lambda_chat._chunks_for_document_mode(
            "summarize_page", {"page_range": "2-2"}, "", chunks[:4],
        )
        self.assertEqual(["a"], [chunk["chunk_id"] for chunk in page_chunks])
        chapter_chunks = lambda_chat._chunks_for_document_mode(
            "summarize_chapter", {}, "", chunks[:4],
        )
        self.assertEqual(["a", "b", "d", "e"], [
            chunk["chunk_id"] for chunk in chapter_chunks
        ])
        self.assertIn("chapter-wise summary", lambda_chat._build_summarization_question(
            "summarize_chapter", {},
        ).lower())
        chapter_question = lambda_chat._build_summarization_question(
            "summarize_chapter", {},
        ).lower()
        self.assertIn("summarize every identified chapter separately", chapter_question)
        self.assertNotIn("summarize only", chapter_question)
        self.assertEqual((5, 20), lambda_chat._parse_page_range("5–20"))
        self.assertIsNone(lambda_chat._parse_page_range("20-5"))
        self.assertIsNone(lambda_chat._parse_page_range("all"))

    def test_page_and_chapter_summaries_answer_each_document_with_own_sources(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        chunks = [
            {"document_id": "doc-1", "chunk_id": "old-1", "filename": "one.pdf", "page": 4, "text": "Chapter 3: Previous"},
            {"document_id": "doc-1", "chunk_id": "page-1", "filename": "one.pdf", "page": 5, "text": "Page five evidence"},
            {"document_id": "doc-1", "chunk_id": "page-2", "filename": "one.pdf", "page": 6, "text": "Page six evidence"},
            {"document_id": "doc-1", "chunk_id": "chapter-1", "filename": "one.pdf", "page": 9, "text": "Chapter 4: Vision\nVision evidence"},
            {"document_id": "doc-1", "chunk_id": "body-1", "filename": "one.pdf", "page": 10, "text": "More vision evidence"},
            {"document_id": "doc-1", "chunk_id": "next-1", "filename": "one.pdf", "page": 11, "text": "Chapter 5: Roles"},
            {"document_id": "doc-2", "chunk_id": "old-2", "filename": "two.pdf", "page": 4, "text": "Chapter 3: Previous"},
            {"document_id": "doc-2", "chunk_id": "page-3", "filename": "two.pdf", "page": 5, "text": "Page five evidence"},
            {"document_id": "doc-2", "chunk_id": "chapter-2", "filename": "two.pdf", "page": 12, "text": "Chapter 4: Outcomes\nOutcomes evidence"},
            {"document_id": "doc-2", "chunk_id": "body-2", "filename": "two.pdf", "page": 13, "text": "More outcomes evidence"},
            {"document_id": "doc-2", "chunk_id": "next-2", "filename": "two.pdf", "page": 14, "text": "Chapter 5: Roles"},
        ]

        for mode, body, selected_chunks in (
            ("summarize_page", {"page_range": "5-6"}, {"doc-1": {"page-1", "page-2"}, "doc-2": {"page-3"}}),
            ("summarize_chapter", {}, {
                "doc-1": {"old-1", "page-1", "page-2", "chapter-1", "body-1", "next-1"},
                "doc-2": {"old-2", "page-3", "chapter-2", "body-2", "next-2"},
            }),
        ):
            seen = {}

            def summarize(**kwargs):
                supplied = kwargs["context_chunks"]
                document_id = supplied[0]["document_id"]
                seen[document_id] = {chunk["chunk_id"] for chunk in supplied}
                return {
                    "text": f"Evidence from {document_id} [src1]",
                    "citations": [{
                        "source_id": "src1",
                        "document_id": document_id,
                        "filename": supplied[0]["filename"],
                    }],
                }

            with self.subTest(mode=mode), patch.object(
                bedrock_model, "answer_question", side_effect=summarize,
            ) as mocked:
                result = lambda_chat._answer_each_document(
                    model_id, ["doc-1", "doc-2"], chunks, mode, body,
                    lambda_chat._build_summarization_question(mode, body), [],
                )
                self.assertEqual(2, mocked.call_count)
                self.assertEqual(selected_chunks, seen)
                self.assertEqual(["one.pdf", "two.pdf"], [
                    item["filename"] for item in result["document_summaries"]
                ])
                self.assertEqual(["doc-1", "doc-2"], [
                    item["citations"][0]["document_id"]
                    for item in result["document_summaries"]
                ])

    def test_chapter_summary_does_not_require_detectable_chapter_headings(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        chunks = [{
            "document_id": "doc-1",
            "chunk_id": "a",
            "filename": "one.pdf",
            "page": 1,
            "text": "Chapter 1: Introduction",
        }]
        with patch.object(bedrock_model, "answer_question", return_value={
            "text": "Introduction [src1]",
            "citations": [{"source_id": "src1", "document_id": "doc-1"}],
        }) as mocked:
            result = lambda_chat._answer_each_document(
                model_id, ["doc-1"], chunks, "summarize_chapter",
                {}, lambda_chat._build_summarization_question("summarize_chapter", {}), [],
            )
        self.assertEqual("one.pdf", result["document_summaries"][0]["filename"])
        self.assertEqual("Introduction [src1]", result["document_summaries"][0]["text"])
        mocked.assert_called_once()

    def test_chunk_range_mode_is_removed(self):
        self.assertNotIn("summarize_chunk", lambda_chat.SUPPORTED_MODES)
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        response = lambda_chat.lambda_handler({
            "httpMethod": "POST",
            "body": json.dumps({
                "document_ids": ["doc-1"],
                "model_ids": [model_id],
                "mode": "summarize_chunk",
                "chunk_range": "1-3",
            }),
        }, None)
        self.assertEqual(400, response["statusCode"])

    def test_page_summary_rejects_malformed_range_before_starting_job(self):
        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        response = lambda_chat.lambda_handler({
            "httpMethod": "POST",
            "body": json.dumps({
                "document_ids": ["doc-1"],
                "model_ids": [model_id],
                "mode": "summarize_page",
                "page_range": "20-5",
            }),
        }, None)
        self.assertEqual(400, response["statusCode"])
        self.assertIn("page_range", json.loads(response["body"])["error"])

    def test_prompt_requires_inline_citations_for_each_claim(self):
        system, messages = bedrock_model._build_prompt(
            "What happened?",
            [{
                "source_id": "src1",
                "document_id": "doc-1",
                "filename": "history.pdf",
                "page": 8,
                "content_type": "table",
                "text": "Year | Rainfall\n1934 | 500 mm",
            }],
            [],
        )
        self.assertIn("Cite every factual paragraph and every factual bullet", system)
        self.assertIn("Never collect citations at the end", system)
        self.assertIn("[src1]", system)
        self.assertIn("page=8, type=table", messages[-1]["content"][0]["text"])
        self.assertIn("1934 | 500 mm", messages[-1]["content"][0]["text"])

    def test_answer_only_returns_sources_cited_inline_by_the_model(self):
        class FakeBedrock:
            def converse(self, **_kwargs):
                return {
                    "output": {"message": {"content": [{"text": "The rainfall was 500 mm. [src1]"}]}},
                    "usage": {"inputTokens": 10, "outputTokens": 5},
                }

        model_id = bedrock_model.BEDROCK_MODELS[0]["model_id"]
        chunks = [
            {"source_id": "src1", "document_id": "doc-1", "filename": "one.pdf", "page": 1, "text": "500 mm"},
            {"source_id": "src2", "document_id": "doc-1", "filename": "one.pdf", "page": 2, "text": "Other evidence"},
        ]
        with patch.object(bedrock_model, "_bedrock_client", return_value=FakeBedrock()):
            answer = bedrock_model.answer_question(model_id, "How much rain?", chunks)
        self.assertEqual(["src1"], [citation["source_id"] for citation in answer["citations"]])
        self.assertIn("[src1]", answer["text"])

    def test_trailing_citations_are_relocated_to_supporting_lines(self):
        chunks = [
            {"source_id": "src1", "text": "Heat waves caused high temperatures and deaths."},
            {"source_id": "src2", "text": "Vulnerable communities need health services."},
        ]
        raw = (
            "### Findings\n"
            "- Heat waves caused high temperatures and deaths.\n"
            "- Vulnerable communities need health services.\n"
            "[src1, src2]"
        )
        result = bedrock_model._add_inline_citations(raw, chunks)
        self.assertIn("- Heat waves caused high temperatures and deaths. [src1]", result)
        self.assertIn("- Vulnerable communities need health services. [src2]", result)
        self.assertNotIn("\n[src1, src2]", result)

    def test_invalid_or_unsupported_citations_are_not_fabricated(self):
        result = bedrock_model._add_inline_citations(
            "Unrelated astronomy claim [src99]",
            [{"source_id": "src1", "text": "Local rainfall figures for the district."}],
        )
        self.assertNotIn("[src99]", result)
        self.assertNotIn("[src1]", result)


if __name__ == "__main__":
    unittest.main()
