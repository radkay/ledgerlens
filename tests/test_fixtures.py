import hashlib
import unittest

from provisioning.fixtures import bootstrap_fixture, synthetic_identifier


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
