import hashlib
import json
import unittest
from collections import defaultdict

from provisioning.fixtures import bootstrap_fixture, demo_fixtures
from src.ledgerlens.extraction import extract_digital_pdf, field_id
from src.ledgerlens.processing.replay import classify


class DemoFixtureTests(unittest.TestCase):
    def test_demo_has_three_matched_people_and_a_review_document(self):
        fixtures = demo_fixtures()
        self.assertEqual(fixtures, demo_fixtures())
        self.assertEqual(fixtures[0], bootstrap_fixture())
        self.assertEqual(len(fixtures), 7)
        self.assertEqual(len({entry["fixture_id"] for _, entry in fixtures}), 7)
        self.assertEqual(len({entry["sha256"] for _, entry in fixtures}), 7)

        expected_forms = defaultdict(set)
        actual_forms = defaultdict(set)
        identifiers = defaultdict(set)
        review_documents = []
        for pdf, entry in fixtures:
            with self.subTest(fixture=entry["fixture_id"]):
                person = entry["person_key"]
                form = entry["form_type"]
                doc_id = hashlib.sha256(pdf).hexdigest()
                self.assertEqual(entry["sha256"], doc_id)
                self.assertEqual(entry["filename"], f"{doc_id}.pdf")
                self.assertEqual(entry["variant"], "digital")
                expected_forms[person].add(tuple(entry["expected_forms"]))

                attempt = extract_digital_pdf(pdf, doc_id, "demo-fixture-test")
                self.assertEqual(attempt["status"], "extracted")
                extracted = {
                    field_id(doc_id, field["field_name"], field["occurrence"]):
                    field["extracted_value"]
                    for field in json.loads(attempt["fields_json"])
                }
                expected = {
                    field["field_id"]: field["expected_value"] for field in entry["fields"]
                }
                for field in entry["fields"]:
                    name = field["field_name"]
                    value = field["expected_value"]
                    if name.endswith("_ssn"):
                        self.assertRegex(value, r"^000-\d{2}-\d{4}$")
                        identifiers[person].add(value)
                    elif name.endswith(("_ein", "_tin")):
                        self.assertRegex(value, r"^00-\d{7}$")

                disposition = classify(attempt, form)
                if disposition == "needs_review":
                    self.assertEqual(form, "W-2")
                    review_documents.append(entry["fixture_id"])
                    wage_id = field_id(doc_id, "wages", 1)
                    self.assertEqual(extracted[wage_id], "1875.OO")
                    self.assertEqual(expected[wage_id], "1875.00")
                    self.assertEqual(extracted | {wage_id: expected[wage_id]}, expected)
                else:
                    self.assertEqual(disposition, "auto_approved")
                    self.assertEqual(extracted, expected)
                    actual_forms[person].add(form)

        self.assertEqual(review_documents, ["demo-w2-person-003-review-digital"])
        self.assertEqual(len(actual_forms), 3)
        for person, forms in actual_forms.items():
            self.assertIn(forms, ({"W-2", "1099"}, {"W-2", "1098"}))
            self.assertEqual(len(expected_forms[person]), 1)
            self.assertEqual(set(next(iter(expected_forms[person]))), forms)
            self.assertEqual(len(identifiers[person]), 1)
        self.assertEqual(len(set.union(*identifiers.values())), 3)
