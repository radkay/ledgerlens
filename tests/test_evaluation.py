"""Evaluator boundary and extraction accuracy tests."""

from datetime import timedelta
import json
import unittest

from tests.test_processing import DOC, START, MemoryStore
from ledgerlens.evaluation.job import ACCURACY, ANSWER_KEY, READS, evaluate
from ledgerlens.processing.job import DOCUMENTS, EXTRACTIONS, PUBLICATIONS, WRITES
from ledgerlens.processing.replay import field_id


class EvaluatorStore(MemoryStore):
    def read(self, table, **kwargs):
        if table not in (*READS, ACCURACY):
            raise AssertionError("Unexpected evaluator read")
        return super().read(table, **kwargs)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.store = EvaluatorStore()
        self.store.append(DOCUMENTS, [{"doc_id": DOC, "received_at": START}])
        self.store.append(ANSWER_KEY, [
            {"fixture_id": "digital", "doc_id": DOC, "field_id": field_id(DOC, name, 1),
             "field_name": name, "expected_value": expected, "form_type": "W-2", "variant": "digital"}
            for name, expected in (
                ("wages", "25000.00"), ("employee_ssn", "000-12-0001"), ("employer_ein", "00-1200001"),
            )
        ])

    def publication(self, run_id, sequence):
        self.store.append(PUBLICATIONS, [{
            "run_id": run_id, "published_at": START + timedelta(hours=sequence),
            "publication_seq": sequence, "input_digest": run_id,
        }])

    def extraction(self, identifier, *, wage, ssn=None, status="needs_review", minute=0):
        fields = [
            {"field_name": name, "occurrence": 1, "extracted_value": value}
            for name, value in (("wages", wage), ("employee_ssn", ssn)) if value is not None
        ]
        self.store.append(EXTRACTIONS, [{
            "doc_id": DOC, "extraction_id": identifier, "status": status,
            "extracted_at": START + timedelta(minutes=minute),
            "fields_json": json.dumps(fields),
        }])

    def test_counts_missing_fields_and_uses_only_original_extraction(self):
        self.extraction("z", wage="25100.00", ssn="000-12-0001")
        self.extraction("a", wage="25000.00", minute=5)
        self.publication("released", 1)
        self.assertEqual(evaluate(self.store), "released")
        self.assertEqual(evaluate(self.store), "released")
        self.assertEqual(self.store.read(ACCURACY), [{
            "run_id": "released", "form_type": "W-2", "variant": "digital",
            "expected_count": 3, "exact_match_count": 1,
        }])
        self.assertEqual(self.store.appended.count(ACCURACY), 1)

    def test_no_publication_is_pending_and_future_attempt_is_outside_cutoff(self):
        self.assertIsNone(evaluate(self.store))
        self.assertEqual(self.store.read(ACCURACY), [])
        self.extraction("future", wage="25000.00", minute=90)
        self.publication("earlier", 1)
        self.assertEqual(evaluate(self.store), "earlier")
        self.publication("later", 2)
        self.assertEqual(evaluate(self.store), "later")
        self.assertEqual([(row["run_id"], row["exact_match_count"]) for row in self.store.read(ACCURACY)],
                         [("earlier", 0), ("later", 1)])

    def test_failed_first_attempt_and_unreceived_fixture_still_count_in_denominator(self):
        self.extraction("failed", wage=None, status="failed")
        self.extraction("new", wage="25000.00", minute=5)
        self.store.append(ANSWER_KEY, [{
            "fixture_id": "scanned", "doc_id": "unreceived", "field_id": "unreceived-field",
            "field_name": "wages", "expected_value": "1.00", "form_type": "W-2", "variant": "scanned",
        }])
        self.publication("released", 1)
        evaluate(self.store)
        self.assertEqual([(row["variant"], row["expected_count"], row["exact_match_count"])
                          for row in self.store.read(ACCURACY)],
                         [("digital", 3, 0), ("scanned", 1, 0)])

    def test_permissions_boundary(self):
        from ledgerlens.processing.job import INPUTS

        self.assertNotIn(ANSWER_KEY, (*INPUTS, *WRITES))
        self.assertNotIn(ACCURACY, (*INPUTS, *WRITES))
        self.assertIn(ANSWER_KEY, READS)
        self.assertNotIn("silver.fields", READS)
        self.assertNotIn("review.decisions", READS)


if __name__ == "__main__":
    unittest.main()
