import unittest

from src.ledgerlens.app.ledgerlens_ui.outputs import GoldValue, Lineage, default_gold_value_index, lineage_display


class LineagePresentationTests(unittest.TestCase):
    def test_defaults_to_the_first_amount_including_zero_and_skips_null_amounts(self):
        status = GoldValue("status-row", "status", "missing", "expected W-2", "fixture-person-001", "2025")
        null_amount = GoldValue("null-row", "wages", None, "person-year total", "fixture-person-001", "2025")
        zero = GoldValue("zero-row", "wages", "0.00", "pre-filled W-2", "fixture-person-001", "2025")
        amount = GoldValue("amount-row", "wages", "25000.00", "person-year total", "fixture-person-002", "2025")

        self.assertEqual(default_gold_value_index([status, null_amount, zero, amount]), 2)
        self.assertEqual(default_gold_value_index([status]), 0)
        self.assertEqual(null_amount.label, "wages · — · person-year total · fixture-person-001 · 2025")

    def test_table_orders_columns_and_preserves_full_identifiers_separately(self):
        link = Lineage("a" * 64, "wages", "b" * 64, "c" * 64, "extraction-1", "d" * 32,
                       "fixture", "1", "model", None)
        display, full = lineage_display([link])
        row, = display
        self.assertEqual(tuple(row), (
            "output_field", "extractor_name", "extractor_version", "decision_id", "doc_id",
            "field_id", "extraction_id", "gold_row_id", "model_name", "model_version",
        ))
        self.assertEqual(row, {
            "output_field": "wages", "extractor_name": "fixture", "extractor_version": "1",
            "decision_id": "d" * 12, "doc_id": "b" * 12, "field_id": "c" * 12,
            "extraction_id": "extraction-1", "gold_row_id": "a" * 12,
            "model_name": "model", "model_version": None,
        })
        self.assertEqual(full, [{
            "decision_id": "d" * 32, "doc_id": "b" * 64, "field_id": "c" * 64, "gold_row_id": "a" * 64,
        }])
