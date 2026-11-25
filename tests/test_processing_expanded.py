"""Acceptance coverage for the expanded local corpus and confidence publication."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from pypdf import __version__

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledgerlens.extraction import extract_digital_pdf
from ledgerlens.processing.adapters import managed_extractor_plan
from ledgerlens.processing.job import DECISIONS, DOCUMENTS, EXTRACTIONS, INVENTORY, PUBLICATIONS, RUNS, publish
from ledgerlens.processing.replay import field_id
from ledgerlens.processing.snapshot import GRAINS, build_snapshot
from provisioning.fixtures import (
    bootstrap_fixture, digital_fixtures, identifier_preview_fixtures, scanned_fixtures,
)
from tests.test_processing import PERSON, START, MemoryInbox, MemoryStore

CONFIDENCE = "gold.confidence_summary"


class ExpandedReplayTests(unittest.TestCase):
    def test_replay_and_cached_repairs_on_the_full_fixture_matrix(self):
        corpus = [*digital_fixtures(), *scanned_fixtures(), *identifier_preview_fixtures()]
        self.assertEqual(Counter(entry["variant"] for _, entry in corpus),
                         {"digital": 4, "scanned": 4})
        originals = {}
        for pdf, entry in corpus:
            if entry["variant"] == "digital":
                attempt = extract_digital_pdf(pdf, entry["sha256"], "local")
                text = "\n".join(json.loads(attempt["raw_json"])["pages"])
                originals[entry["fixture_id"].removesuffix("-digital")] = text.splitlines()

        def scenario(reverse):
            store, inbox = MemoryStore(), MemoryInbox()
            existing = {(row["person_key"], row["tax_year"], row["expected_form"])
                        for row in store.read(INVENTORY)}
            desired = {(entry["person_key"], entry["tax_year"], form) for _, entry in corpus
                       for form in entry["expected_forms"]}
            store.append(INVENTORY, [
                {"person_key": person, "tax_year": year, "expected_form": form}
                for person, year, form in sorted(desired - existing)
            ])
            ordered = corpus[::-1] if reverse else corpus
            inbox.pdfs = {entry["sha256"]: pdf for pdf, entry in ordered}
            inbox.extra_names = [entry["filename"] for _, entry in ordered] * 3

            scans = {entry["sha256"]: originals[entry["fixture_id"].removesuffix("-scanned")]
                     for _, entry in corpus if entry["variant"] == "scanned"}
            calls = []

            def parse(_sql, *, args):
                doc_id = sha256(args["document"]).hexdigest()
                calls.append(doc_id)
                result = Mock()
                result.first.return_value = {"parsed_json": json.dumps({
                    "document": {"elements": [
                        {"id": index, "type": "text", "content": line}
                        for index, line in enumerate(scans[doc_id])
                    ]},
                })}
                return result

            spark = Mock()
            spark.sql.side_effect = parse
            plan = managed_extractor_plan(spark)
            tick = 0

            def clock():
                nonlocal tick
                tick += 1
                return START + timedelta(seconds=tick)

            def invoke(invocation, trigger="manual"):
                return publish(store, inbox, plan, job_run_id=invocation, trigger_type=trigger, clock=clock)

            first = invoke("1")
            self.assertEqual(invoke("2", "scheduled"), first)
            self.assertEqual(len(store.read(DOCUMENTS)), len(corpus))
            self.assertEqual(Counter(calls), {doc_id: 1 for doc_id in scans})
            extraction_ids = {row["doc_id"]: row["extraction_id"] for row in store.read(EXTRACTIONS)}
            fixtures = {entry["fixture_id"]: entry["sha256"] for _, entry in corpus}
            w2_scan = fixtures["bootstrap-w2-scanned"]
            w2_digital = fixtures["bootstrap-w2-digital"]
            income_scan = fixtures["bootstrap-1099-scanned"]
            mortgage_scan = fixtures["bootstrap-1098-scanned"]

            def decision(prefix, doc_id, action, reason, *, value=None, at=None):
                return {
                    "decision_id": prefix, "doc_id": doc_id,
                    "extraction_id": extraction_ids[doc_id], "action": action, "reason_code": reason,
                    "field_id": field_id(doc_id, "wages", 1) if value else None,
                    "corrected_value": value, "actor_id": "synthetic-reviewer",
                    "decided_at": at or clock(),
                }

            at = clock()
            correction = decision("b-correct", w2_scan, "correct_field", "extraction_error",
                                  value="25123.00", at=at)
            actions = [
                decision("a-approve", w2_scan, "approve", "verified", at=at),
                correction, correction,
                decision("c-recall", w2_digital, "recall", "release_correction"),
                decision("d-reject", income_scan, "reject", "invalid_document"),
                decision("e-reprocess", mortgage_scan, "reprocess", "low_confidence"),
            ]
            store.append(DECISIONS, actions[::-1] if reverse else actions)
            store.failure = PUBLICATIONS
            with self.assertRaisesRegex(RuntimeError, "interrupted append"):
                invoke("3")
            self.assertEqual(store.read(PUBLICATIONS)[-1]["run_id"], first)
            self.assertEqual(Counter(calls)[mortgage_scan], 2)
            before_retry = calls[:]
            second = invoke("3")
            self.assertEqual(invoke("4", "scheduled"), second)
            self.assertEqual(calls, before_retry)
            self.assertEqual(len(store.read(EXTRACTIONS)), len(corpus) + 1)
            wages, = [row["wages"] for row in store.serving("gold.person_year")
                      if row["person_key"] == PERSON]
            self.assertEqual(wages, Decimal("25123.00"))

            store.append(DECISIONS, [correction | {"corrected_value": "1.00"}])
            with self.assertRaisesRegex(ValueError, "Conflicting duplicate decision"):
                invoke("5")
            self.assertEqual(store.read(PUBLICATIONS)[-1]["run_id"], second)
            return second, {table: store.serving(table) for table in GRAINS}

        self.assertEqual(scenario(False), scenario(True))


class ConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.store, self.inbox = MemoryStore(), MemoryInbox()
        pdf, entry = bootstrap_fixture()
        self.inbox.pdfs = {entry["sha256"]: pdf}
        self.scores = {"wages": 0.8, "employee_ssn": 0.5, "employer_ein": None}

    def extract(self, *args):
        result = extract_digital_pdf(*args)
        fields = json.loads(result["fields_json"])
        for field in fields:
            field["confidence"] = self.scores[field["field_name"]]
        return result | {"fields_json": json.dumps(fields)}

    def invoke(self, invocation):
        return publish(self.store, self.inbox, self.extract, extractor_version=__version__,
                       job_run_id=invocation, trigger_type="manual", clock=lambda: START)

    def test_confidence_is_validated_and_written_before_the_marker(self):
        self.store.failure = PUBLICATIONS
        with self.assertRaisesRegex(RuntimeError, "interrupted append"):
            self.invoke("1")
        self.assertEqual(self.store.serving(CONFIDENCE), [])
        rows = self.store.read(CONFIDENCE)
        self.assertEqual({row["confidence_bucket"]: row["count"] for row in rows},
                         {"[0.5, 0.8)": 1, "[0.8, 1]": 1, "Unreported": 1})
        self.assertLess(self.store.appended.index(CONFIDENCE),
                        self.store.appended.index(PUBLICATIONS))
        run_id = self.invoke("1")
        self.assertEqual(self.store.serving(CONFIDENCE), rows)
        self.assertTrue(all(row["run_id"] == run_id for row in rows))

    def test_new_snapshot_revision_fills_confidence_without_reextracting(self):
        with patch("ledgerlens.processing.job.PROCESSING_REVISION", 1):
            previous = self.invoke("1")
        # The previous revision published without any confidence rows.
        self.store.history[CONFIDENCE][-1] = []
        with patch.object(self, "extract", side_effect=AssertionError("unexpected extraction")):
            current = self.invoke("2")
        self.assertNotEqual(previous, current)
        self.assertEqual(len(self.store.read(EXTRACTIONS)), 1)
        self.assertEqual(self.store.read(CONFIDENCE, run_id=previous), [])
        self.assertEqual(sum(row["count"] for row in self.store.serving(CONFIDENCE)), 3)

    def test_invalid_scores_and_corrupt_counts_prevent_publication(self):
        self.scores["wages"] = 1.01
        with self.assertRaisesRegex(ValueError, r"confidence is outside \[0, 1\]"):
            self.invoke("1")
        self.assertEqual(self.store.read(PUBLICATIONS), [])
        # Build a fresh synthetic catalog because the first attempt is immutable.
        self.setUp()
        self.scores |= {"wages": 1.0, "employee_ssn": 0.0}

        def wrong_count(*args):
            tables = build_snapshot(*args)
            tables[CONFIDENCE][0]["count"] += 1
            return tables

        with patch("ledgerlens.processing.job.build_snapshot", side_effect=wrong_count):
            with self.assertRaisesRegex(ValueError, "Confidence buckets do not match"):
                self.invoke("2")
        self.assertEqual(self.store.read(PUBLICATIONS), [])
        self.assertEqual(self.store.read(RUNS)[-1]["detail_code"], "snapshot_validation")
        self.invoke("2")
        self.assertEqual({row["confidence_bucket"] for row in self.store.serving(CONFIDENCE)},
                         {"[0, 0.5)", "[0.8, 1]", "Unreported"})
