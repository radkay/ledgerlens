import hashlib
import json
import unittest
from io import BytesIO

from provisioning.fixtures import (
    bootstrap_fixture, digital_fixtures, scanned_fixtures, synthetic_identifier,
)
from src.ledgerlens.extraction import extract_digital_pdf, field_id


class SyntheticFixtureTests(unittest.TestCase):
    def test_identifiers_use_unissued_ranges(self):
        for serial in (1, 42, 9999):
            with self.subTest(serial=serial):
                self.assertRegex(synthetic_identifier("ssn", serial), r"^000-\d{2}-\d{4}$")
                self.assertRegex(synthetic_identifier("ein", serial), r"^00-\d{7}$")

        for invalid in (0, 10000):
            with self.assertRaises(ValueError):
                synthetic_identifier("ssn", invalid)

    def test_fixture_content_matches_manifest(self):
        pdf, entry = bootstrap_fixture()
        self.assertEqual(bootstrap_fixture(), (pdf, entry))
        self.assertEqual(entry["sha256"], hashlib.sha256(pdf).hexdigest())
        self.assertEqual(entry["filename"], entry["sha256"] + ".pdf")
        fields = {item["field_name"]: item["expected_value"] for item in entry["fields"]}
        self.assertRegex(fields["employee_ssn"], r"^000-\d{2}-\d{4}$")
        self.assertRegex(fields["employer_ein"], r"^00-\d{7}$")
        for value in fields.values():
            self.assertIn(value.encode(), pdf)

    def test_digital_fixture_set_preserves_bootstrap_and_inventory(self):
        fixtures = digital_fixtures()
        self.assertEqual(fixtures, digital_fixtures())
        self.assertEqual(fixtures[0], bootstrap_fixture())
        self.assertEqual([entry["form_type"] for _, entry in fixtures], ["W-2", "1099", "1098"])
        self.assertEqual(len({entry["sha256"] for _, entry in fixtures}), len(fixtures))
        for pdf, entry in fixtures:
            with self.subTest(form=entry["form_type"]):
                self.assertIn(entry["form_type"], entry["expected_forms"])
                self.assertEqual(entry["sha256"], hashlib.sha256(pdf).hexdigest())
                self.assertEqual(entry["filename"], entry["sha256"] + ".pdf")
                for field in entry["fields"]:
                    self.assertIn(field["expected_value"].encode(), pdf)
                    name = field["field_name"]
                    if name.endswith("_ssn"):
                        self.assertRegex(field["expected_value"], r"^000-\d{2}-\d{4}$")
                    if name.endswith("_tin"):
                        self.assertRegex(field["expected_value"], r"^00-\d{7}$")


    def test_scanned_fixtures_are_raster_only_and_have_separate_identities(self):
        from pypdf import PdfReader

        scans = scanned_fixtures()
        self.assertEqual(scans, scanned_fixtures())
        self.assertEqual(len(scans), 3)
        for (pdf, scan), (_, digital) in zip(scans, digital_fixtures(), strict=True):
            with self.subTest(form=scan["form_type"]):
                self.assertEqual(scan["variant"], "scanned")
                self.assertNotEqual(scan["fixture_id"], digital["fixture_id"])
                self.assertEqual(scan["sha256"], hashlib.sha256(pdf).hexdigest())
                self.assertEqual(scan["filename"], scan["sha256"] + ".pdf")
                self.assertNotEqual(scan["sha256"], digital["sha256"])
                for key in ("form_type", "person_key", "expected_forms", "tax_year"):
                    self.assertEqual(scan[key], digital[key])

                reader = PdfReader(BytesIO(pdf), strict=True)
                self.assertEqual(len(reader.pages), 1)
                page = reader.pages[0]
                self.assertEqual(page.extract_text(), "")
                self.assertNotIn("/Font", page["/Resources"])
                self.assertIsNone(reader.metadata)
                image = page["/Resources"]["/XObject"]["/Scan"]
                self.assertEqual(image["/Subtype"], "/Image")
                pixels = image.get_data()
                self.assertEqual(len(pixels), image["/Width"] * image["/Height"])
                self.assertEqual(max(pixels), 255)
                self.assertLess(min(pixels), 32)
                record = extract_digital_pdf(pdf, scan["sha256"], "run-1")
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["error_code"], "no_text_layer")
                self.assertEqual(json.loads(record["fields_json"]), [])

                for field, original in zip(scan["fields"], digital["fields"], strict=True):
                    name = field["field_name"]
                    value = field["expected_value"]
                    self.assertEqual((name, value), (original["field_name"], original["expected_value"]))
                    self.assertEqual(field["field_id"], field_id(scan["sha256"], name, 1))
                    self.assertNotEqual(field["field_id"], original["field_id"])
                    self.assertNotIn(value.encode("ascii"), pdf)
                    if name.endswith("_ssn"):
                        self.assertRegex(value, r"^000-\d{2}-\d{4}$")
                    if name.endswith(("_ein", "_tin")):
                        self.assertRegex(value, r"^00-\d{7}$")
