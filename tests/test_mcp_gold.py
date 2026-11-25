import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.gold import FETCH_LIMIT, ROW_LIMIT, GoldTools


class GoldToolsTests(unittest.TestCase):
    def setUp(self):
        self.read = Mock()
        self.tools = GoldTools("ledgerlens_dev", self.read)

    def test_reuses_pinned_run_and_only_reads_allowlisted_views(self):
        trace = ["a" * 64, "person", "2024", "W-2", "wages", "13.00", "doc",
                 "field", "attempt", "decision", "extractor", "1", None, None]
        self.read.side_effect = [[["published"]], [trace], [["2"]], [["doc", "needs_review", "now"]]]
        gold = self.tools.pin()
        traced = gold.trace_value("a" * 64, "wages")
        review = gold.documents_needing_review()
        self.assertEqual(traced["rows"][0]["decision_id"], "decision")
        self.assertEqual(review, {
            "run_id": "published", "published_document_count": 2,
            "live_documents": [{"doc_id": "doc", "status": "needs_review", "state_at": "now"}],
            "live_documents_truncated": False,
        })

        calls = self.read.call_args_list
        objects = set()
        for call in calls:
            sql, params = call.args
            objects.update(re.findall(r"\b\w+\.\w+\.\w+\b", sql))
            self.assertIn(f"LIMIT {call.kwargs['row_limit']}", sql)
            self.assertEqual(set(re.findall(r":(\w+)", sql)), params.keys())
        self.assertEqual(objects, {
            f"ledgerlens_dev.gold.{view}" for view in (
                "latest_publication", "current_person_year", "current_prefill",
                "current_missing_documents", "current_lineage", "current_review_summary",
                "current_document_state",
            )
        })
        self.assertEqual(calls[1].args[1], {
            "run_id": "published", "gold_row_id": "a" * 64, "output_field": "wages",
        })
        self.assertNotIn("a" * 64, calls[1].args[0])
        self.assertEqual(calls[2].args[1], {"run_id": "published", "status": "needs_review"})
        self.assertEqual(calls[3].args[1], {"status": "needs_review"})

    def test_rejects_denied_objects_and_invalid_parameters(self):
        for catalog in ("", "ledgerlens_prod", "ledgerlens_dev.silver", "ledgerlens_dev; SELECT 1"):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                GoldTools(catalog, self.read)
        self.read.return_value = [["published"]]
        gold = self.tools.pin()
        for row_id, field in (
            ("a" * 63, "wages"), (123, "wages"), (None, "wages"), ("'", ""),
            ("", "ledgerlens_dev.bronze.documents"), ("", "ledgerlens_dev.silver.fields"),
            ("", "ledgerlens_dev.eval.answer_key"), ("", None), ("", "wages' OR 1=1"),
        ):
            with self.subTest(row_id=row_id, field=field), self.assertRaises(ValueError):
                gold.trace_value(row_id, field)
        self.read.assert_called_once()

    def test_trims_with_truncation_flag_and_preserves_missing_document_outputs(self):
        missing = ["a" * 64, "person", "2024", "1099", "status", "missing", *([None] * 8)]
        live = ["doc", "needs_review", "now"]
        self.read.side_effect = [[["published"]], [missing] * FETCH_LIMIT, [], [live] * FETCH_LIMIT]
        gold = self.tools.pin()
        result = gold.trace_value(output_field="status")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["rows"]), ROW_LIMIT)
        self.assertEqual(result["rows"][0]["output_value"], "missing")
        self.assertIsNone(result["rows"][0]["doc_id"])

        review = gold.documents_needing_review()
        self.assertEqual(review["published_document_count"], 0)
        self.assertTrue(review["live_documents_truncated"])
        self.assertEqual(len(review["live_documents"]), ROW_LIMIT)

    def test_fails_closed_when_no_publication_or_sql_exceeds_bound(self):
        self.read.return_value = []
        with self.assertRaises(LookupError):
            self.tools.pin()
        self.read.return_value = [["run"], ["extra"]]
        with self.assertRaises(RuntimeError):
            self.tools.pin()
