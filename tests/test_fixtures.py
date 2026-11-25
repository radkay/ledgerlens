import hashlib
import unittest

from provisioning.fixtures import bootstrap_fixture, digital_fixtures, synthetic_identifier


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
