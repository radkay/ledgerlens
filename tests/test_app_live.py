import sqlite3
import sys
import unittest
from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.ledgerlens.app.ledgerlens_ui import live
from src.ledgerlens.app.ledgerlens_ui.databricks import Workspace as DatabricksWorkspace
from src.ledgerlens.app.ledgerlens_ui.dashboard import Confidence, latest_batch_event, read_overview
from src.ledgerlens.app.ledgerlens_ui.live import GoldReader, LiveBatch, LiveWorkbench, Sql
from src.ledgerlens.app.ledgerlens_ui.workbench import Decision, ReviewDocument


class Workspace:
    def __init__(self):
        self.trace = []
        self.calls = []
        self.responses = []
        self.last_http_status = 200

    def call(self, method, path, token, **kwargs):
        self.calls.append((method, path, token, kwargs))
        return self.responses.pop(0)

    def preview_listing(self, path, token, params):
        return self.call("LIST", path, token, params=params)

    def preview_pdf(self, path, token):
        return self.call("PDF", path, token)


def result(rows):
    return {"status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}}


class LiveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.reader_sql = Sql(self.workspace, "warehouse", lambda: "reader")
        self.operator_sql = Sql(self.workspace, "warehouse", lambda: "operator")
        self.timestamp = datetime(2025, 11, 23, tzinfo=UTC)

    def test_cold_warehouse_polls_one_statement_and_fetches_chunks(self):
        self.workspace.responses = [
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {"status": {"state": "RUNNING"}},
            {"status": {"state": "SUCCEEDED"}, "result": {"data_array": [["first"]], "next_chunk_index": 1}},
            {"data_array": [["second"]]},
        ]
        notice = []

        @contextmanager
        def waiting():
            notice.append(("show", len(self.workspace.calls)))
            try:
                yield
            finally:
                notice.append(("clear", len(self.workspace.calls)))

        sql = Sql(self.workspace, "warehouse", lambda: "reader", waiting=waiting)
        with patch.object(live, "sleep") as delay:
            self.assertEqual(sql.execute("SELECT :value", value="example"), [["first"], ["second"]])
        self.assertEqual(notice, [("show", 1), ("clear", 3)])
        delay.assert_called_once_with(2)
        post, *gets = self.workspace.calls
        self.assertEqual(post[3]["json"]["on_wait_timeout"], "CONTINUE")
        self.assertEqual(post[3]["json"]["wait_timeout"], "10s")
        self.assertEqual([(method, path, token) for method, path, token, _ in gets], [
            ("GET", "/api/2.0/sql/statements/statement-1", "reader"),
            ("GET", "/api/2.0/sql/statements/statement-1", "reader"),
            ("GET", "/api/2.0/sql/statements/statement-1/result/chunks/1", "reader"),
        ])

    def test_cold_warehouse_stops_at_deadline(self):
        self.workspace.responses = [
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {"status": {"state": "RUNNING"}},
        ]
        stderr = StringIO()
        with (patch.object(live, "monotonic", side_effect=[0, 119, 119.5, 120]),
              patch.object(live, "sleep") as delay, redirect_stderr(stderr)):
            with self.assertRaises(TimeoutError):
                self.reader_sql.execute("SELECT :value", value="secret-fixture")
        delay.assert_called_once_with(0.5)
        self.assertEqual(len(self.workspace.calls), 2)
        self.assertEqual(self.workspace.calls[1][3]["timeout"], (0.5, 0.5))
        self.assertEqual(stderr.getvalue(), "Databricks exception=TimeoutError http_status=200\n")

    def test_terminal_failure_clears_notice_and_logs_only_safe_metadata(self):
        self.workspace.responses = [
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {"status": {"state": "FAILED", "error": {"message": "secret-fixture"}}},
        ]
        notice = MagicMock()
        sql = Sql(self.workspace, "warehouse", lambda: "reader", waiting=notice)
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaisesRegex(OSError, "did not confirm"):
            sql.execute("SELECT :value", value="secret-fixture")
        notice.return_value.__enter__.assert_called_once()
        notice.return_value.__exit__.assert_called_once()
        self.assertEqual(stderr.getvalue(), "Databricks exception=OSError http_status=200\n")
        self.assertEqual(len(self.workspace.calls), 2)

    def test_workspace_logs_http_errors_without_request_or_response_content(self):
        class RequestException(OSError):
            pass

        session = MagicMock()
        response = session.__enter__.return_value.request.return_value.__enter__.return_value
        response.status_code = 503
        response.text = "secret-response"
        requests = SimpleNamespace(Session=lambda: session, RequestException=RequestException)
        workspace = DatabricksWorkspace("https://fixture.example")
        stderr = StringIO()
        with patch.dict(sys.modules, {"requests": requests}), redirect_stderr(stderr):
            with self.assertRaisesRegex(OSError, "did not complete"):
                workspace.call("GET", "/api/2.0/sql/statements/secret-statement", "secret-token")
        self.assertEqual(stderr.getvalue(), "Databricks exception=OSError http_status=503\n")
        self.assertEqual(workspace.trace[-1], ("GET /api/2.0/sql/statements/{statement_id}", None))

    def test_public_reads_are_pinned_and_use_only_gold(self):
        self.workspace.responses = [
            result([["publication-1", "7", self.timestamp.isoformat()]]),
            result([["received", "2"], ["released", "1"]]),
            result([["needs_review", "3"]]),
            result([["W-2", "[0.8, 1]", "2"]]),
            result([]),
        ]
        reader = GoldReader(self.reader_sql, "demo")
        publication, live, review, confidence, accuracy = read_overview(reader)
        self.assertEqual(publication.run_id, "publication-1")
        self.assertEqual(live["received"], 2)
        self.assertEqual(review["needs_review"], 3)
        self.assertEqual(confidence, [Confidence("W-2", "[0.8, 1]", 2)])
        self.assertEqual(accuracy, [])
        statements = [call[3]["json"] for call in self.workspace.calls]
        self.assertTrue(all("ledgerlens_demo.gold." in body["statement"] for body in statements))
        self.assertTrue(all(call[2] == "reader" for call in self.workspace.calls))
        self.assertEqual(sum("latest_publication" in body["statement"] for body in statements), 1)
        self.assertIn("current_document_state", statements[1]["statement"])
        for body, view in zip(statements[2:], ("review_summary", "confidence_summary", "extraction_accuracy"), strict=True):
            self.assertIn(f".gold.published_{view}", body["statement"])
            self.assertEqual(body["parameters"], [{"name": "run_id", "value": "publication-1", "type": "STRING"}])

    def test_pinned_output_reads_keep_prior_run_when_another_publication_appears(self):
        self.workspace.responses = [
            result([["publication-1", "7", self.timestamp.isoformat()]]),
            result([["publication-2", "8", self.timestamp.isoformat()]]),
            result([["person-1", "2025", "25000.00", None, None]]),
            result([["row-1", "wages", "25000.00", "person-year total", "person-1", "2025"]]),
        ]
        reader = GoldReader(self.reader_sql, "demo")
        pinned = reader.latest_publication()
        self.assertEqual(reader.latest_publication().run_id, "publication-2")
        self.assertEqual(reader.person_years(pinned.run_id)[0].wages, "25000.00")
        self.assertEqual(reader.lineage_values(pinned.run_id)[0].output_value, "25000.00")
        for call in self.workspace.calls[2:]:
            request = call[3]["json"]
            self.assertIn(".gold.published_", request["statement"])
            self.assertNotIn(".gold.current_", request["statement"])
            self.assertEqual(request["parameters"], [
                {"name": "run_id", "value": "publication-1", "type": "STRING"},
            ])

    def test_coverage_reads_gold_for_one_publication_and_keeps_missing_only_taxpayers(self):
        self.workspace.responses = [
            result([["person-1", "2025", "0.00", None, "250.21"]]),
            result([["person-2", "2025", "1099", "missing"]]),
        ]
        reader = GoldReader(self.reader_sql, "demo")
        total, = reader.person_years("publication-1")
        expected, = reader.expected_documents("publication-1")
        self.assertEqual((total.tax_year, total.wages, total.other_income), (2025, "0.00", None))
        self.assertEqual((expected.person_key, expected.status), ("person-2", "missing"))
        for call, view in zip(self.workspace.calls, ("published_person_year", "published_missing_documents"), strict=True):
            payload = call[3]["json"]
            self.assertIn(f"ledgerlens_demo.gold.{view} WHERE run_id = :run_id", payload["statement"])
            self.assertEqual(payload["parameters"], [{"name": "run_id", "value": "publication-1", "type": "STRING"}])
            self.assertEqual(call[2], "reader")

    def test_reverse_lineage_binds_the_exact_output_field_and_publication(self):
        self.workspace.responses = [
            result([["row-1", "wages", "25000.00", "person-year total", "person-1", "2025"],
                    ["row-2", "status", "missing", "expected W-2", "person-2", "2025"],
                    ["row-3", "wages", "25000.00", "pre-filled W-2", "person-1", "2025"]]),
            result([]),
        ]
        reader = GoldReader(self.reader_sql, "demo")
        wage, value, prefill = reader.lineage_values("publication-1")
        self.assertEqual(wage.label, "wages · 25000.00 · person-year total · person-1 · 2025")
        self.assertEqual(prefill.label, "wages · 25000.00 · pre-filled W-2 · person-1 · 2025")
        self.assertEqual(value.label, "status · missing · expected W-2 · person-2 · 2025")
        self.assertEqual(reader.lineage("publication-1", gold_row_id=value.gold_row_id,
                                        output_field=value.output_field), [])
        index_query = self.workspace.calls[0][3]["json"]["statement"]
        self.assertEqual(index_query.count(":run_id"), 5)
        self.assertIn("UNION ALL", index_query)
        self.assertIn("concat('pre-filled ', f.form_type)", index_query)
        self.assertIn("coalesce(f.person_key, d.person_key, p.person_key)", index_query)
        request = self.workspace.calls[1][3]["json"]
        self.assertIn("gold_row_id = :gold_row_id AND output_field = :output_field", request["statement"])
        self.assertEqual({item["name"]: item["value"] for item in request["parameters"]},
                         {"run_id": "publication-1", "gold_row_id": "row-2", "output_field": "status"})

    def test_lineage_picker_joins_source_context_within_the_pinned_run(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.create_function("concat", 2, lambda left, right: left + right)
        db.executescript("""
            CREATE TABLE published_lineage(run_id, gold_row_id, output_field);
            CREATE TABLE published_person_year(
                run_id, gold_row_id, person_key, tax_year, wages, other_income, mortgage_interest
            );
            CREATE TABLE published_prefill(
                run_id, gold_row_id, output_field, output_value, form_type, person_key, tax_year
            );
            CREATE TABLE published_missing_documents(run_id, gold_row_id, status, person_key, tax_year, expected_form);
            INSERT INTO published_lineage VALUES ('pinned', 'total', 'wages'), ('pinned', 'form', 'wages');
            INSERT INTO published_person_year VALUES
                ('pinned', 'total', 'fixture-person-001', 2025, '25000.00', NULL, NULL),
                ('new', 'total', 'fixture-person-001', 2025, '90000.00', NULL, NULL);
            INSERT INTO published_prefill VALUES
                ('pinned', 'form', 'wages', '25000.00', 'W-2', 'fixture-person-001', 2025),
                ('new', 'form', 'wages', '90000.00', 'W-2', 'fixture-person-001', 2025);
            INSERT INTO published_missing_documents VALUES
                ('pinned', 'missing', 'missing', 'fixture-person-001', 2025, '1099');
        """)

        class OfflineSql:
            def execute(self, statement, **values):
                query = statement.replace("ledgerlens_demo.gold.", "").replace(" AS STRING)", " AS TEXT)")
                return db.execute(query, values)

        values = GoldReader(OfflineSql(), "demo").lineage_values("pinned")
        self.assertEqual([value.label for value in values], [
            "status · missing · expected 1099 · fixture-person-001 · 2025",
            "wages · 25000.00 · pre-filled W-2 · fixture-person-001 · 2025",
            "wages · 25000.00 · person-year total · fixture-person-001 · 2025",
        ])

    def test_prefill_reads_only_the_selected_form_in_the_pinned_publication(self):
        self.workspace.responses = [
            result([["fixture-person", "2025", "W-2"]]),
            result([["gold-1", "fixture-person", "2025", "W-2", "wages", "25000.00"]]),
        ]
        reader = GoldReader(self.reader_sql, "demo")
        form = reader.prefill_forms("publication-1")[0]
        self.assertEqual(reader.prefill("publication-1", form)[0].output_value, "25000.00")
        statement = self.workspace.calls[-1][3]["json"]
        self.assertEqual({item["name"]: item["value"] for item in statement["parameters"]},
                         {"run_id": "publication-1", "person_key": "fixture-person",
                          "tax_year": "2025", "form_type": "W-2"})

    def test_batch_uses_delegated_token_explicit_job_and_ops_events(self):
        self.workspace.responses = [
            {"settings": {"name": "ledgerlens-dev-process", "max_concurrent_runs": 1}},
            {"run_id": 87},
            result([["logical-run", "4", self.timestamp.isoformat(), "validated", "87", "manual", None]]),
        ]
        batch = LiveBatch(self.operator_sql, "dev", 19)
        run = batch.run_batch("idempotency-id")
        event = latest_batch_event(batch, run)
        self.assertEqual(event.status, "validated")
        self.assertEqual(run, "87")
        self.assertEqual(self.workspace.calls[1][3]["json"]["job_id"], 19)
        self.assertEqual(self.workspace.calls[1][3]["json"]["idempotency_token"], "idempotency-id")
        body = self.workspace.calls[2][3]["json"]
        self.assertIn("ledgerlens_dev.ops.pipeline_runs", body["statement"])
        self.assertEqual(body["parameters"], [{"name": "job_run_id", "value": "87", "type": "STRING"}])
        self.assertTrue(all(call[2] == "operator" for call in self.workspace.calls))

    def test_workbench_appends_parameterized_decision(self):
        self.workspace.responses = [
            result([["doc", "extract", self.timestamp.isoformat(), "needs_review",
                     "field", "wages", "25000.00", "25000.00", "0.46"]]),
            result([["correct_field", "extraction_error"]]),
            result([["1"]]),
        ]
        operator = LiveWorkbench(self.operator_sql, "dev", "verified-user")
        decision = Decision("decision", "doc", "extract", "field", "correct_field",
                            "extraction_error", "25001.00", "verified-user", self.timestamp)
        operator.append_decision(decision, self.timestamp)
        body = self.workspace.calls[-1][3]["json"]
        self.assertIn("INSERT INTO ledgerlens_dev.review.decisions", body["statement"])
        self.assertNotIn("25001.00", body["statement"])
        self.assertNotIn("25001.00", str(self.workspace.trace))
        self.assertEqual({item["name"]: item["value"] for item in body["parameters"]}["corrected_value"], "25001.00")

    def test_workbench_reads_only_contract_masked_pages_for_the_current_extraction(self):
        operator = LiveWorkbench(self.operator_sql, "dev", "verified-user")
        document = ReviewDocument("a" * 64, "current-extraction", self.timestamp, "needs_review")
        self.workspace.responses = [
            {"contents": [{"name": "0.pdf", "is_directory": False},
                          {"name": "1.pdf", "is_directory": True},
                          {"name": "original.pdf", "is_directory": False}],
             "next_page_token": "second"},
            {"contents": [{"name": "2.pdf", "is_directory": False}]},
            b"%PDF-masked",
            None,
        ]
        self.assertEqual(operator.preview_pages(document), [0, 2])
        self.assertEqual(operator.preview(document, 0), b"%PDF-masked")
        self.assertIsNone(operator.preview(document, 1))
        directory = f"/Volumes/ledgerlens_dev/review/masked_previews/{document.doc_id}/current-extraction"
        self.assertEqual(self.workspace.calls, [
            ("LIST", directory, "operator", {"params": {"page_size": 1000}}),
            ("LIST", directory, "operator", {"params": {"page_size": 1000, "page_token": "second"}}),
            ("PDF", f"{directory}/0.pdf", "operator", {}),
            ("PDF", f"{directory}/1.pdf", "operator", {}),
        ])
        for invalid in (replace(document, extraction_id=None), replace(document, extraction_id="../bronze")):
            self.assertEqual(operator.preview_pages(invalid), [])
            self.assertIsNone(operator.preview(invalid, 0))
