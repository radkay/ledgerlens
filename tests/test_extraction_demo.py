import hashlib
import json
import unittest
from collections import defaultdict

from provisioning.fixtures import bootstrap_fixture, demo_fixtures, raster_smoke_fixture
from src.ledgerlens.extraction import extract_digital_pdf, field_id
from src.ledgerlens.processing.replay import classify


class DemoFixtureTests(unittest.TestCase):
    def test_demo_has_matched_people_across_years_and_two_review_documents(self):
        fixtures = demo_fixtures()
        self.assertEqual(fixtures, demo_fixtures())
        self.assertEqual(fixtures[0], bootstrap_fixture())
        self.assertEqual(len(fixtures), 12)
        self.assertEqual(len({entry["fixture_id"] for _, entry in fixtures}), 12)
        self.assertEqual(len({entry["sha256"] for _, entry in fixtures}), 12)

        expected_forms = defaultdict(set)
        actual_forms = defaultdict(set)
        identifiers = defaultdict(set)
        review_documents = []
        edge_amounts = {}
        for pdf, entry in fixtures:
            with self.subTest(fixture=entry["fixture_id"]):
                person = entry["person_key"]
                year = entry["tax_year"]
                form = entry["form_type"]
                doc_id = hashlib.sha256(pdf).hexdigest()
                self.assertEqual(entry["sha256"], doc_id)
                self.assertEqual(entry["filename"], f"{doc_id}.pdf")
                self.assertEqual(entry["variant"], "digital")
                expected_forms[person, year].add(tuple(entry["expected_forms"]))

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
                    review_documents.append(entry["fixture_id"])
                    if form == "W-2":
                        wage_id = field_id(doc_id, "wages", 1)
                        self.assertEqual(extracted[wage_id], "1875.OO")
                        self.assertEqual(expected[wage_id], "1875.00")
                        self.assertEqual(extracted | {wage_id: expected[wage_id]}, expected)
                    else:
                        self.assertEqual(form, "1098")
                        missing_id = field_id(doc_id, "mortgage_interest", 1)
                        self.assertNotIn(missing_id, extracted)
                        self.assertEqual(expected[missing_id], "125.00")
                        self.assertEqual(extracted | {missing_id: "125.00"}, expected)
                else:
                    self.assertEqual(disposition, "auto_approved")
                    self.assertEqual(extracted, expected)
                    actual_forms[person, year].add(form)
                    if year == 2024 and form != "W-2":
                        edge_amounts[form] = next(
                            extracted[field["field_id"]] for field in entry["fields"]
                            if field["field_name"] in {"other_income", "mortgage_interest"}
                        )

        self.assertEqual(review_documents, [
            "demo-w2-person-003-review-digital", "demo-1098-person-002-2024-missing-interest-digital",
        ])
        self.assertEqual(edge_amounts, {"1099": "0.00", "1098": "0.01"})
        self.assertEqual({year for _, year in actual_forms}, {2024, 2025})
        self.assertEqual(len(actual_forms), 5)
        for person_year, forms in actual_forms.items():
            self.assertIn(forms, ({"W-2", "1099"}, {"W-2", "1098"}))
            self.assertEqual(len(expected_forms[person_year]), 1)
            self.assertEqual(set(next(iter(expected_forms[person_year]))), forms)
            self.assertEqual(len(identifiers[person_year[0]]), 1)
        self.assertEqual(len(set.union(*identifiers.values())), 3)


    def test_raster_smoke_fixture_has_its_own_identity_and_answer_key(self):
        pdf, entry = raster_smoke_fixture()
        self.assertEqual((pdf, entry), raster_smoke_fixture())
        self.assertEqual(entry["variant"], "scanned")
        self.assertEqual(entry["sha256"], hashlib.sha256(pdf).hexdigest())
        self.assertNotIn(entry["person_key"], {item["person_key"] for _, item in demo_fixtures()})
        self.assertEqual(entry["expected_forms"], ["W-2"])
        attempt = extract_digital_pdf(pdf, entry["sha256"], "raster-smoke-test")
        self.assertEqual((attempt["status"], attempt["error_code"]), ("failed", "no_text_layer"))
        self.assertEqual(len(entry["fields"]), 3)
        for field in entry["fields"]:
            self.assertEqual(field["field_id"], field_id(entry["sha256"], field["field_name"], 1))
            self.assertNotIn(field["expected_value"].encode("ascii"), pdf)
