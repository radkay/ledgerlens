"""Adapter routing can be verified independently of the workspace AI function."""

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledgerlens.processing.adapters import ExtractorPlan, Stage, managed_extractor_plan
from ledgerlens.processing.job import EXTRACTIONS, INVENTORY, PUBLICATIONS, publish, run
from ledgerlens.processing.replay import AMOUNT_FIELDS, FORM_IDENTIFIERS
from tests.test_processing import MemoryInbox, MemoryStore, PERSON, START


class ExtractorPlanTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def stage(self, name, *, error=None, confidence=None, fallback=(), require_confidence=False):
        def extract(contents, doc_id, run_id):
            self.calls.append((name, contents, doc_id, run_id))
            return {
                "doc_id": doc_id, "run_id": run_id, "extractor_name": name,
                "extractor_version": "test", "status": "failed" if error else "extracted",
                "error_code": error,
                "fields_json": json.dumps([] if error else [
                    {"field_name": "wages", "occurrence": 1, "extracted_value": "25000.00",
                     "confidence": confidence, "bbox": None},
                ]),
            }

        return Stage(name, "test", extract, frozenset(fallback), require_confidence)

    def test_digital_success_stops_without_calling_raster_or_ai(self):
        plan = ExtractorPlan((self.stage("pypdf"), self.stage("raster"),
                              self.stage("ai_parse_document", require_confidence=True)), 0.8)
        result = plan(b"synthetic digital", "doc", "run")
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(self.calls, [("pypdf", b"synthetic digital", "doc", "run")])

    def test_no_text_falls_through_to_raster_then_ai_and_low_confidence_needs_review(self):
        plan = ExtractorPlan((
            self.stage("pypdf", error="no_text_layer", fallback=("no_text_layer",)),
            self.stage("raster", error="no_fields", fallback=("no_fields",)),
            self.stage("ai_parse_document", confidence=0.71, require_confidence=True),
        ), 0.8)
        result = plan(b"synthetic raster", "doc", "run")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["extractor_name"], "ai_parse_document")
        self.assertEqual([call[0] for call in self.calls], ["pypdf", "raster", "ai_parse_document"])
        self.assertEqual(plan.configuration()[1][-1], ["ai_parse_document", "test", [], True, False])

    def test_terminal_digital_failure_and_unreported_managed_confidence(self):
        plan = ExtractorPlan((
            self.stage("pypdf", error="invalid_pdf", fallback=("no_text_layer",)),
            self.stage("ai_parse_document", require_confidence=True),
        ), 0.8)
        self.assertEqual(plan(b"bad PDF", "doc", "run")["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        managed = ExtractorPlan((self.stage("ai_parse_document", require_confidence=True),), 0.8)
        self.assertEqual(managed(b"synthetic raster", "doc", "run")["status"], "needs_review")

    def test_mixed_inbox_publishes_only_released_forms_and_reuses_saved_attempts(self):
        store, inbox = MemoryStore(), MemoryInbox()
        inputs = {form.encode("ascii"): form for form in AMOUNT_FIELDS}
        inbox.pdfs = {sha256(pdf).hexdigest(): pdf for pdf in inputs}
        other_person = "fixture-person-002"
        store.append(INVENTORY, [{"person_key": other_person, "tax_year": 2025,
                                  "expected_form": "1098"}])

        def adapter(name, contents, doc_id, run_id):
            form = inputs[contents]
            self.calls.append((name, form))
            failure = None
            if form == "1099" and name != "ai_parse_document":
                failure = "no_text_layer" if name == "pypdf" else "no_fields"
            amount_field = AMOUNT_FIELDS[form]
            person = other_person if form == "1098" else PERSON
            values = {identifier: "synthetic-identifier" for identifier in FORM_IDENTIFIERS[form]}
            values[amount_field] = "250.00"
            lines = [f"Synthetic {form} - 2025 - LedgerLens Sample", f"Fixture person key: {person}"]
            raw = {"pages": ["\n".join(lines)]}
            if name == "ai_parse_document":
                raw = {"document": {"elements": [
                    {"id": index, "type": "text", "content": line}
                    for index, line in enumerate(lines)
                ]}}
            return {
                "doc_id": doc_id, "run_id": run_id, "extractor_name": name,
                "extractor_version": "test", "status": "failed" if failure else "extracted",
                "error_code": failure, "extraction_id": f"{name}:{doc_id}:{run_id}",
                "model_name": "test-model" if name == "ai_parse_document" else None,
                "model_version": None,
                "raw_json": json.dumps(raw),
                "fields_json": json.dumps([] if failure else [
                    {"field_name": field, "occurrence": 1, "extracted_value": value,
                     "confidence": 0.71 if name == "ai_parse_document" else None, "bbox": None}
                    for field, value in sorted(values.items())
                ]),
            }

        def stage(name, reasons=(), require_confidence=False):
            def extract(*args):
                return adapter(name, *args)

            return Stage(name, "test", extract, frozenset(reasons), require_confidence)

        plan = ExtractorPlan((
            stage("pypdf", ("no_text_layer",)),
            stage("raster", ("no_fields",)),
            stage("ai_parse_document", require_confidence=True),
        ), 0.8)

        def run():
            return publish(store, inbox, plan, job_run_id="synthetic", trigger_type="manual",
                           clock=lambda: START + timedelta(hours=1))

        store.failure = PUBLICATIONS
        with self.assertRaisesRegex(RuntimeError, "interrupted append"):
            run()
        self.assertEqual(store.serving("gold.person_year"), [])
        calls_after_failure = self.calls[:]
        self.assertEqual(len(store.read(EXTRACTIONS)), 3)
        publication = run()
        self.assertEqual(self.calls, calls_after_failure)
        rows = {row["person_key"]: row for row in store.serving("gold.person_year")}
        self.assertEqual(rows[PERSON]["wages"], Decimal("250.00"))
        self.assertIsNone(rows[PERSON]["other_income"])
        self.assertEqual(rows[other_person]["mortgage_interest"], Decimal("250.00"))
        fields = store.read("silver.fields", run_id=publication)
        self.assertEqual({row["status"] for row in fields if row["form_type"] == "1099"},
                         {"needs_review"})
        self.assertEqual([call[0] for call in self.calls if call[1] == "1099"],
                         ["pypdf", "raster", "ai_parse_document"])

    def test_locked_digital_adapter_publishes_all_seeded_forms(self):
        from pypdf import __version__
        from provisioning.fixtures import digital_fixtures
        from ledgerlens.extraction import extract_digital_pdf

        store, inbox = MemoryStore(), MemoryInbox()
        fixtures = digital_fixtures()
        inbox.pdfs = {entry["sha256"]: pdf for pdf, entry in fixtures}
        store.append(INVENTORY, [
            {"person_key": "fixture-person-002", "tax_year": 2025, "expected_form": "1098"},
        ])
        publish(store, inbox, extract_digital_pdf, extractor_version=__version__,
                job_run_id="digital-fixtures", trigger_type="manual", clock=lambda: START)

        self.assertEqual(len(store.read(EXTRACTIONS)), 3)
        rows = {row["person_key"]: row for row in store.serving("gold.person_year")}
        self.assertEqual(rows[PERSON]["wages"], Decimal("25000.00"))
        self.assertEqual(rows[PERSON]["other_income"], Decimal("1150.00"))
        self.assertEqual(rows["fixture-person-002"]["mortgage_interest"], Decimal("4800.50"))
        self.assertEqual({row["status"] for row in store.serving("gold.missing_documents")}, {"present"})
        self.assertEqual({row["output_field"] for row in store.serving("gold.prefill")}, set(AMOUNT_FIELDS.values()))
        identifiers = [row for row in store.serving("silver.fields") if row["field_type"] == "string"]
        self.assertEqual(len(identifiers), 6)
        self.assertTrue(all(row["extracted_value"] is None and row["final_decimal"] is None
                            for row in identifiers))

    def test_real_raster_pdf_dispatches_to_managed_adapter_and_requires_review(self):
        from ledgerlens.extraction import extract_digital_pdf
        from provisioning.fixtures import bootstrap_fixture, scanned_fixtures

        store, inbox = MemoryStore(), MemoryInbox()
        digital, digital_entry = bootstrap_fixture()
        raster, raster_entry = scanned_fixtures()[0]
        inbox.pdfs = {digital_entry["sha256"]: digital, raster_entry["sha256"]: raster}
        original = extract_digital_pdf(digital, digital_entry["sha256"], "original")
        lines = json.loads(original["raw_json"])["pages"][0].splitlines()

        spark = Mock()
        spark.sql.return_value.first.return_value = {"parsed_json": json.dumps({
            "document": {"elements": [
                {"id": index, "type": "title" if index == 0 else "text",
                 "content": line, "confidence": 0.71}
                for index, line in enumerate(lines)
            ]},
        })}
        with patch("ledgerlens.processing.job.DeltaStore", return_value=store), patch(
            "ledgerlens.processing.job.Inbox", return_value=inbox,
        ):
            run_id = run(spark, "ledgerlens_dev", "real-raster", "one_time")
            self.assertEqual(run(spark, "ledgerlens_dev", "same-content", "periodic"), run_id)
            store.append(INVENTORY, [{"person_key": PERSON, "tax_year": 2025, "expected_form": "1098"}])
            self.assertNotEqual(run(spark, "ledgerlens_dev", "new-inventory", "one_time"), run_id)
        spark.sql.assert_called_once()
        self.assertEqual(spark.sql.call_args.kwargs, {"args": {"document": bytearray(raster)}})
        attempts = {row["doc_id"]: row for row in store.read(EXTRACTIONS)}
        self.assertEqual(attempts[raster_entry["sha256"]]["status"], "needs_review")
        self.assertEqual(attempts[digital_entry["sha256"]]["extractor_name"], "pypdf")
        silver = store.read("silver.fields", run_id=run_id)
        raster_fields = [row for row in silver if row["doc_id"] == raster_entry["sha256"]]
        self.assertEqual(len(raster_fields), 3)
        self.assertTrue(all(row["confidence"] is None for row in raster_fields))
        row, = store.serving("gold.person_year")
        self.assertEqual(row["wages"], Decimal("25000.00"))

    def test_unassociated_managed_pdf_stays_in_review_without_gold_values(self):
        from provisioning.fixtures import scanned_fixtures

        store, inbox = MemoryStore(), MemoryInbox()
        raster, fixture = scanned_fixtures()[0]
        inbox.pdfs = {fixture["sha256"]: raster}
        spark = Mock()
        spark.sql.return_value.first.return_value = {"parsed_json": json.dumps({
            "document": {"elements": [
                {"id": 0, "type": "title", "content": "Synthetic W-2 - 2025"},
                {"id": 1, "type": "text", "content": "Wages: 25000.00"},
            ]},
        })}
        publish(store, inbox, managed_extractor_plan(spark), job_run_id="no-metadata",
                trigger_type="manual", clock=lambda: START)
        attempt, = store.read(EXTRACTIONS)
        self.assertEqual(attempt["status"], "needs_review")
        self.assertEqual(store.serving("gold.review_summary")[0]["status"], "needs_review")
        self.assertEqual(store.serving("gold.person_year"), [])
        self.assertEqual(store.serving("silver.fields"), [])

    def test_vector_only_pdf_does_not_call_managed_parser(self):
        from reportlab.pdfgen.canvas import Canvas

        output = BytesIO()
        canvas = Canvas(output, invariant=1)
        canvas.rect(10, 10, 30, 30)
        canvas.save()
        pdf = output.getvalue()
        spark = Mock()
        attempt = managed_extractor_plan(spark)(pdf, sha256(pdf).hexdigest(), "vector")
        self.assertEqual(attempt["error_code"], "no_text_layer")
        spark.sql.assert_not_called()

    def test_interrupted_scan_batch_reuses_the_previous_managed_result(self):
        from ledgerlens.extraction import extract_digital_pdf
        from provisioning.fixtures import digital_fixtures, scanned_fixtures

        store, inbox = MemoryStore(), MemoryInbox()
        inbox.pdfs.clear()
        responses = {}
        for (raster, entry), (digital, source) in zip(
            scanned_fixtures()[:2], digital_fixtures()[:2], strict=True,
        ):
            inbox.pdfs[entry["sha256"]] = raster
            original = extract_digital_pdf(digital, source["sha256"], "original")
            lines = json.loads(original["raw_json"])["pages"][0].splitlines()
            responses[entry["sha256"]] = json.dumps({"document": {"elements": [
                {"type": "text", "id": index, "content": line} for index, line in enumerate(lines)
            ]}})

        calls = []

        def parse(_statement, *, args):
            doc_id = sha256(args["document"]).hexdigest()
            calls.append(doc_id)
            if len(calls) == 2:
                raise RuntimeError("simulated second scan failure")
            response = Mock()
            response.first.return_value = {"parsed_json": responses[doc_id]}
            return response

        spark = Mock()
        spark.sql.side_effect = parse
        plan = managed_extractor_plan(spark)

        def invoke():
            return publish(store, inbox, plan, job_run_id="scan-batch", trigger_type="manual",
                           clock=lambda: START)

        with self.assertRaisesRegex(RuntimeError, "second scan failure"):
            invoke()
        first, = store.read(EXTRACTIONS)
        self.assertEqual(first["doc_id"], calls[0])
        self.assertEqual(store.read(PUBLICATIONS), [])
        invoke()
        self.assertEqual(calls, [*sorted(inbox.pdfs), max(inbox.pdfs)])
        self.assertEqual(len(store.read(EXTRACTIONS)), 2)

    def test_repair_preserves_the_original_digital_only_plan(self):
        from ledgerlens.extraction import extract_digital_pdf
        from provisioning.fixtures import bootstrap_fixture
        from pypdf import __version__

        store, inbox = MemoryStore(), MemoryInbox()
        pdf, entry = bootstrap_fixture()
        inbox.pdfs = {entry["sha256"]: pdf}
        store.failure = PUBLICATIONS
        with self.assertRaisesRegex(RuntimeError, "interrupted append"):
            publish(store, inbox, extract_digital_pdf, extractor_version=__version__,
                    job_run_id="legacy", trigger_type="manual", clock=lambda: START)

        spark = Mock()
        run_id = publish(store, inbox, managed_extractor_plan(spark), job_run_id="legacy",
                         trigger_type="manual", clock=lambda: START)
        attempt, = store.read(EXTRACTIONS)
        self.assertEqual(attempt["run_id"], run_id)
        spark.sql.assert_not_called()
