"""Contract-level tests for frozen inputs, review replay and publication."""

from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledgerlens.processing.job import (
    DECISIONS, DOCUMENTS, EXTRACTIONS, INVENTORY, PUBLICATIONS, REASONS, RELEASES, RUNS, publish,
)
from ledgerlens.processing.replay import ACTIONS, field_id
from ledgerlens.processing.snapshot import silver_projection

START = datetime(2025, 1, 1, tzinfo=UTC)
PERSON = "fixture-person-001"
PDF = b"synthetic digital W-2 test bytes"
DOC = sha256(PDF).hexdigest()
WAGES = field_id(DOC, "wages", 1)
REASON_CODES = dict(zip(
    ACTIONS, ("verified", "extraction_error", "invalid_document", "low_confidence", "release_correction"),
    strict=True,
))


class MemoryStore:
    """Append-only store with immutable table versions and the Silver read mask."""

    def __init__(self):
        self.history = defaultdict(lambda: [[]])
        self.failure = None
        self.appended = []
        self.append(INVENTORY, [
            {"person_key": PERSON, "tax_year": 2025, "expected_form": form}
            for form in ("W-2", "1099")
        ])
        self.append(REASONS, [
            {"action": action, "reason_code": reason, "enabled": True, "description": reason}
            for action, reason in REASON_CODES.items()
        ])

    def version(self, table):
        return len(self.history[table]) - 1

    def read(self, table, *, version=None, run_id=None):
        snapshot = self.history[table][-1 if version is None else version]
        rows = [deepcopy(row) for row in snapshot if run_id is None or row["run_id"] == run_id]
        return [silver_projection(row) for row in rows] if table == "silver.fields" else rows

    def append(self, table, rows):
        if not rows:
            return
        self.appended.append(table)
        if self.failure == table:
            self.failure = None
            raise RuntimeError("simulated interrupted append")
        self.history[table].append(self.history[table][-1] + deepcopy(rows))

    def serving(self, table):
        publications = self.read(PUBLICATIONS)
        return self.read(table, run_id=publications[-1]["run_id"]) if publications else []


class MemoryInbox:
    def __init__(self):
        self.pdfs = {DOC: PDF}
        self.extra_names = []

    def files(self):
        return [f"{doc}.pdf" for doc in self.pdfs] + self.extra_names

    def read(self, doc_id):
        return f"/Volumes/ledgerlens_dev/bronze/inbox/{doc_id}.pdf", self.pdfs[doc_id]


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.inbox = MemoryInbox()
        self.tick = 0
        self.wages = "25000.00"
        self.fail_doc = None
        self.extraction_calls = 0

    def clock(self):
        self.tick += 1
        return START + timedelta(seconds=self.tick)

    def extract(self, pdf, doc_id, run_id):
        self.extraction_calls += 1
        self.assertEqual(sha256(pdf).hexdigest(), doc_id)
        fields = {
            "employee_ssn": "000-12-0001", "employer_ein": "00-1200001", "wages": self.wages,
        }
        failed = doc_id == self.fail_doc
        return {
            "run_id": run_id, "doc_id": doc_id, "extraction_id": sha256(
                f"{doc_id}:{run_id}".encode()).hexdigest(),
            "extracted_at": self.clock(), "extractor_name": "pypdf", "extractor_version": "test",
            "model_name": None, "model_version": None, "status": "failed" if failed else "extracted",
            "raw_json": json.dumps({"pages": [
                f"Synthetic W-2 - 2025 - LedgerLens Sample\nFixture person key: {PERSON}\n",
            ]}),
            "fields_json": json.dumps([] if failed else [
                {"field_name": name, "occurrence": 1, "extracted_value": value,
                 "confidence": None, "bbox": None}
                for name, value in fields.items()
            ]),
            "error_code": "invalid_pdf" if failed else None,
        }

    def run_job(self, invocation, trigger="manual"):
        return publish(self.store, self.inbox, self.extract, extractor_version="test",
                       job_run_id=invocation, trigger_type=trigger, clock=self.clock)

    def decision(self, action, *, extraction=None, value=None, decision_id=None):
        row = {
            "decision_id": decision_id or f"d-{self.tick:05d}", "doc_id": DOC,
            "extraction_id": extraction, "field_id": WAGES if action == "correct_field" else None,
            "action": action, "reason_code": REASON_CODES[action], "corrected_value": value,
            "actor_id": "synthetic-reviewer", "decided_at": self.clock(),
        }
        self.store.append(DECISIONS, [row])
        return row

    def trace_wages(self, run_id):
        output, = self.store.read("gold.person_year", run_id=run_id)
        source, = [row for row in self.store.read("gold.lineage", run_id=run_id)
                   if row["gold_row_id"] == output["gold_row_id"] and row["output_field"] == "wages"]
        extraction, = [row for row in self.store.read(EXTRACTIONS)
                       if row["extraction_id"] == source["extraction_id"]]
        original, = [row["extracted_value"] for row in json.loads(extraction["fields_json"])
                     if row["field_name"] == "wages"]
        return output["wages"], source, original

    def test_publish_and_reverse_trace_one_gold_wage(self):
        run_id = self.run_job("1")
        output, source, original = self.trace_wages(run_id)
        self.assertEqual(output, Decimal("25000.00"))
        self.assertEqual(original, "25000.00")
        self.assertEqual((source["doc_id"], source["field_id"], source["decision_id"]), (DOC, WAGES, None))
        self.assertEqual(self.store.appended[-3:], [RUNS, PUBLICATIONS, RUNS])
        self.assertEqual([row["status"] for row in self.store.read(RUNS)],
                         ["queued", "running", "validated", "published"])
        self.assertEqual({row["expected_form"]: row["status"]
                          for row in self.store.serving("gold.missing_documents")},
                         {"W-2": "present", "1099": "missing"})
        self.assertEqual({row["output_field"] for row in self.store.serving("gold.prefill")}, {"wages"})
        self.assertEqual({row["status"] for row in self.store.serving("gold.review_summary")}, {"released"})
        self.assertTrue(all(row["final_decimal"] is None for row in self.store.read("silver.fields")
                            if row["field_name"] != "wages"))

    def test_partial_recall_keeps_last_good_release_and_retry_resumes_exact_frozen_run(self):
        first = self.run_job("1")
        original = self.trace_wages(first)
        self.decision("recall")
        self.store.failure = PUBLICATIONS
        with self.assertRaisesRegex(RuntimeError, "interrupted append"):
            self.run_job("2")
        interrupted = self.store.read(RUNS)[-1]
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(self.store.read(RELEASES)[-1]["status"], "recalled")
        self.assertEqual(self.store.serving("gold.person_year")[0]["run_id"], first)

        self.decision("reprocess")  # New decision must not alter the repaired invocation.
        second = self.run_job("2")
        self.assertEqual(second, interrupted["run_id"])
        self.assertEqual(self.store.serving("gold.person_year"), [])
        self.assertEqual(self.trace_wages(first), original)
        self.assertEqual(len(self.store.read(RELEASES)), 2)

    def test_new_extraction_failure_keeps_last_good_publication_visible(self):
        first = self.run_job("1")
        other = b"bad PDF"
        self.fail_doc = sha256(other).hexdigest()
        self.inbox.pdfs[self.fail_doc] = other
        with self.assertRaisesRegex(ValueError, "Digital extraction failed"):
            self.run_job("2")
        self.assertEqual([row["run_id"] for row in self.store.read(PUBLICATIONS)], [first])
        self.assertEqual(self.store.read(EXTRACTIONS)[-1]["status"], "failed")
        self.assertEqual(self.store.serving("gold.person_year")[0]["run_id"], first)

    def test_duplicate_pdf_and_double_trigger(self):
        self.inbox.extra_names.append(f"{DOC}.pdf")
        first = self.run_job("1")
        second = self.run_job("2", "scheduled")
        self.assertEqual(first, second)
        self.assertEqual(self.extraction_calls, 1)
        self.assertEqual(len(self.store.read(DOCUMENTS)), 1)
        self.assertEqual(len(self.store.read(EXTRACTIONS)), 1)
        self.assertEqual(len(self.store.read(PUBLICATIONS)), 1)
        final = self.store.read(RUNS)[-1]
        self.assertEqual((final["status"], final["trigger_type"], final["job_run_id"]),
                         ("skipped_duplicate", "scheduled", "2"))

    def test_duplicate_decision_and_replay_match_with_original_lineage(self):
        self.wages = "unreadable"
        first = self.run_job("1")
        self.assertEqual(self.store.serving("gold.review_summary")[0]["status"], "needs_review")
        attempt, = self.store.read(EXTRACTIONS)
        self.decision("approve", extraction=attempt["extraction_id"])
        row = self.decision("correct_field", extraction=attempt["extraction_id"], value="123.45")
        self.store.append(DECISIONS, [row])
        history, tick = deepcopy(self.store.history), self.tick
        winner = self.run_job("2")
        baseline = deepcopy(self.store.history)
        self.store.history, self.tick = history, tick
        self.assertEqual(self.run_job("2"), winner)
        self.assertEqual(self.store.history, baseline)
        self.assertEqual(self.run_job("3"), winner)
        output, source, original = self.trace_wages(winner)
        self.assertEqual((output, source["decision_id"], original),
                         (Decimal("123.45"), row["decision_id"], "unreadable"))
        self.assertEqual(source["extraction_id"], attempt["extraction_id"])
        self.assertEqual(self.store.read("gold.person_year", run_id=first), [])

        self.store.append(DECISIONS, [row | {"corrected_value": "555.00"}])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.run_job("4")
        self.assertEqual(self.store.read(PUBLICATIONS)[-1]["run_id"], winner)

    def test_reject_reprocess_and_ignore_stale_scoped_decision(self):
        self.wages = "unreadable"
        self.run_job("1")
        old_attempt, = self.store.read(EXTRACTIONS)
        old_id = old_attempt["extraction_id"]
        self.decision("reject", extraction=old_id)
        self.run_job("2")
        self.assertEqual(self.store.serving("gold.review_summary")[0]["status"], "rejected")
        self.decision("reprocess", extraction=old_id)
        self.run_job("3")
        self.decision("correct_field", extraction=old_id, value="1.00")
        self.run_job("4")
        self.assertEqual(self.store.serving("gold.review_summary")[0]["status"], "needs_review")
        self.assertEqual(self.store.serving("gold.person_year"), [])
        old, new = self.store.read(EXTRACTIONS)
        self.assertNotEqual(old["extraction_id"], new["extraction_id"])
        self.decision("correct_field", extraction=new["extraction_id"], value="2.00")
        released = self.run_job("5")
        self.assertEqual(self.trace_wages(released)[0], Decimal("2.00"))

    def test_actionable_decision_requires_registered_reason(self):
        self.wages = "unreadable"
        first = self.run_job("1")
        row = self.decision("approve")
        # Replace the input fixture with a submission that bypassed the workbench.
        self.store.history[DECISIONS][-1] = [row | {"reason_code": "unregistered"}]
        with self.assertRaisesRegex(ValueError, "Decision reason is unavailable"):
            self.run_job("2")
        self.assertEqual(self.store.read(PUBLICATIONS)[-1]["run_id"], first)
