import json
import unittest
from dataclasses import replace
from datetime import timedelta

from src.ledgerlens.app.ledgerlens_ui.databricks import authenticate_operator
from src.ledgerlens.app.ledgerlens_ui.fixtures import (
    NEEDS_REVIEW, PUBLISHED, RELEASED, FixtureReader, FixtureWorkbench,
)
from src.ledgerlens.app.ledgerlens_ui.outputs import prefill_document
from src.ledgerlens.app.ledgerlens_ui.workbench import Decision, Field


class WorkbenchTests(unittest.TestCase):
    def test_delegated_operator_requires_exact_user_and_environment_group(self):
        user = {"active": True, "id": "operator", "groups": [{"display": "ledgerlens-dev-reviewers"}]}

        class Workspace:
            def call(self, method, path, token):
                if token != "verified-by-databricks":
                    raise PermissionError
                return user

        workspace = Workspace()
        verify = "verified-by-databricks"
        self.assertEqual(authenticate_operator(workspace, "dev", "operator", verify).actor_id, "operator")
        self.assertIsNone(authenticate_operator(workspace, "dev", "operator", "unverified"))
        self.assertIsNone(authenticate_operator(workspace, "dev", "different-user", verify))
        self.assertIsNone(authenticate_operator(workspace, "demo", "operator", verify))
        self.assertIsNone(authenticate_operator(workspace, "dev", "operator", None))

    def test_masks_identifiers_and_rejects_identifier_correction(self):
        operator = FixtureWorkbench("operator")
        before = operator.document(NEEDS_REVIEW.doc_id)
        field = before.fields[0]
        self.assertEqual(replace(field, extracted_value="000123456").masked().extracted_value, "[masked]")
        self.assertEqual(Field("field", "notes", "SSN 000-12-3456", None, None).masked().extracted_value,
                         "SSN [masked]")
        self.assertIn(b"Employee SSN: [masked]", operator.preview(before, 0))
        self.assertEqual(operator.preview_pages(before), [0])
        self.assertIsNone(operator.preview(before, 1))
        decision = Decision("decision-1", before.doc_id, before.extraction_id, field.field_id,
                            "correct_field", "extraction_error", "000-12-3456", "operator", before.state_at)
        with self.assertRaises(ValueError):
            operator.append_decision(decision, before.state_at)
        self.assertEqual(operator.document(before.doc_id), before)

    def test_enforces_actor_reason_extraction_and_idempotent_append(self):
        operator = FixtureWorkbench("operator")
        document = NEEDS_REVIEW
        decision = Decision("decision-1", document.doc_id, document.extraction_id, document.fields[2].field_id,
                            "correct_field", "extraction_error", "18800.00", "operator",
                            document.state_at + timedelta(seconds=1))
        with self.assertRaises(PermissionError):
            operator.append_decision(replace(decision, actor_id="other"), document.state_at)
        with self.assertRaises(ValueError):
            operator.append_decision(replace(decision, reason_code="invalid_document"), document.state_at)
        with self.assertRaises(ValueError):
            operator.append_decision(replace(decision, extraction_id="stale"), document.state_at)
        operator.append_decision(decision, document.state_at)
        operator.append_decision(decision, document.state_at)
        with self.assertRaises(ValueError):
            operator.append_decision(replace(decision, corrected_value="1"), document.state_at)
        result = operator.document(document.doc_id)
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.fields[2].extracted_value, "18750.00")
        self.assertEqual(result.fields[2].final_value, "18800.00")
        self.assertEqual(FixtureWorkbench("other").document(document.doc_id), document)

    def test_recall_does_not_change_published_prefill_or_lineage(self):
        operator = FixtureWorkbench("operator")
        reader = FixtureReader()
        run_id = PUBLISHED.run_id
        publication = reader.latest_publication()
        lines = reader.lineage(run_id, doc_id=RELEASED.doc_id)
        self.assertEqual(len(lines), 1)
        gold_row_id = lines[0].gold_row_id
        value = reader.lineage_values(run_id)[0]
        self.assertEqual(value.output_value, "25000.00")
        self.assertEqual(reader.lineage(run_id, gold_row_id=gold_row_id, output_field=value.output_field), lines)
        self.assertEqual(reader.lineage(run_id, gold_row_id=gold_row_id, output_field="other_income"), [])
        self.assertEqual(reader.lineage("unpublished", gold_row_id=gold_row_id, output_field=value.output_field), [])
        decision = Decision("decision-1", RELEASED.doc_id, RELEASED.extraction_id, None, "recall",
                            "release_correction", None, "operator", RELEASED.state_at + timedelta(seconds=1))
        operator.append_decision(decision, RELEASED.state_at)
        self.assertEqual(operator.document(RELEASED.doc_id).status, "released")
        self.assertEqual(reader.lineage(run_id, doc_id=RELEASED.doc_id), lines)
        form = reader.prefill_forms(run_id)[0]
        self.assertEqual(reader.prefill("unpublished", form), [])
        self.assertEqual(reader.prefill(run_id, replace(form, tax_year=2024)), [])
        artifact = json.loads(prefill_document(publication, reader.prefill(run_id, form)))
        self.assertEqual(artifact["fields"], {"wages": "25000.00"})
        self.assertEqual(artifact["published_at"], publication.published_at.isoformat())
