import hashlib
import json
import unittest
from datetime import UTC
from io import BytesIO

from provisioning.fixtures import (
    bootstrap_fixture, digital_fixtures, digital_w2, identifier_preview_fixtures,
)
from src.ledgerlens.extraction import extract_digital_pdf, field_id

BOOTSTRAP_SHA256 = "65912382990c61f773d6b2a109533c7e43d657c8dc52c597e40973b071d23dd8"
BOOTSTRAP_FIELD_IDS = {
    "employee_ssn": "baa06e4a52c9a1eba0c93ea3f8b49c5217bdc478f0f04f2b7455b195a8663642",
    "employer_ein": "b0129e1dcc9bce533779059f8b89f08eec6bc5cba9fe836731d7712e0cc5b426",
    "wages": "08b94f20a9e8572c2d5572fa9e1d0494bd828536901dd045ea132d9b1b0721f4",
}
BRONZE_COLUMNS = {
    "extraction_id", "doc_id", "run_id", "extracted_at", "extractor_name",
    "extractor_version", "model_name", "model_version", "status", "raw_json",
    "fields_json", "error_code",
}


class ExtractionIdentityTests(unittest.TestCase):
    def test_bootstrap_fixture_sha_and_field_ids_are_stable(self):
        pdf, fixture = bootstrap_fixture()
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), BOOTSTRAP_SHA256)
        self.assertEqual(fixture["sha256"], BOOTSTRAP_SHA256)
        self.assertEqual(
            {item["field_name"]: item["field_id"] for item in fixture["fields"]},
            BOOTSTRAP_FIELD_IDS,
        )
        for name, expected in BOOTSTRAP_FIELD_IDS.items():
            self.assertEqual(field_id(BOOTSTRAP_SHA256, name, 1), expected)


class DigitalExtractionTests(unittest.TestCase):
    def test_w2_matches_the_private_answer_key(self):
        pdf, fixture = bootstrap_fixture()
        doc_id = fixture["sha256"]
        record = extract_digital_pdf(pdf, doc_id, "run-1")
        self.assertEqual(set(record), BRONZE_COLUMNS)
        self.assertEqual(record["extracted_at"].tzinfo, UTC)
        self.assertEqual(record["status"], "extracted")
        self.assertEqual(record["doc_id"], doc_id)
        self.assertEqual(record["run_id"], "run-1")
        self.assertEqual(record["extractor_name"], "pypdf")
        self.assertIsNotNone(record["extractor_version"])
        for key in ("model_name", "model_version", "error_code"):
            self.assertIsNone(record[key])
        self.assertEqual(len(json.loads(record["raw_json"])["pages"]), 1)
        expected = [
            {
                "field_name": item["field_name"],
                "occurrence": 1,
                "extracted_value": item["expected_value"],
                "confidence": None,
            }
            for item in fixture["fields"]
        ]
        fields = json.loads(record["fields_json"])
        self.assertEqual([{key: value for key, value in field.items() if key != "bbox"}
                          for field in fields], expected)
        for field in fields:
            self.assert_valid_box(field["bbox"], 0)
        retry = extract_digital_pdf(pdf, doc_id, "run-1")
        reprocess = extract_digital_pdf(pdf, doc_id, "run-2")
        self.assertEqual(retry["extraction_id"], record["extraction_id"])
        self.assertNotEqual(reprocess["extraction_id"], record["extraction_id"])

    def assert_valid_box(self, box, page):
        self.assertEqual(set(box), {"page_index", "left", "top", "right", "bottom"})
        self.assertEqual(box["page_index"], page)
        self.assertTrue(0 <= box["left"] < box["right"] <= 1)
        self.assertTrue(0 <= box["top"] < box["bottom"] <= 1)

    def test_coverage_counts_noncanonical_identifiers_and_withholds_scans(self):
        (digital, digital_entry), (scanned, scanned_entry) = identifier_preview_fixtures()
        original = extract_digital_pdf(digital, digital_entry["sha256"], "run-coverage")
        coverage = json.loads(original["raw_json"])["identifier_coverage"]
        self.assertEqual([item["page_index"] for item in coverage], [0, 1])
        for item in coverage:
            self.assertIs(item["complete"], True)
            self.assertEqual(len(item["boxes"]), 2)
            for box in item["boxes"]:
                self.assert_valid_box(box, item["page_index"])
        fields = json.loads(original["fields_json"])
        self.assertEqual([field["bbox"] for field in fields[:2]], coverage[0]["boxes"])

        raster = extract_digital_pdf(scanned, scanned_entry["sha256"], "run-coverage")
        self.assertEqual(raster["error_code"], "no_text_layer")
        self.assertEqual(json.loads(raster["raw_json"])["identifier_coverage"], [
            {"page_index": 0, "complete": False, "boxes": []},
            {"page_index": 1, "complete": False, "boxes": []},
        ])

    def test_coverage_distinguishes_no_identifiers_and_unsupported_geometry(self):
        from pypdf import PdfReader, PdfWriter

        pdf = digital_w2(["Synthetic W-2 - 2025", "Wages: 10.00"])
        reader = PdfReader(BytesIO(pdf), strict=True)
        reader.pages[0].rotate(90)
        writer = PdfWriter()
        writer.append_pages_from_reader(reader)
        output = BytesIO()
        writer.write(output)

        for document, complete in ((pdf, True), (output.getvalue(), False)):
            doc_id = hashlib.sha256(document).hexdigest()
            record = extract_digital_pdf(document, doc_id, "run-coverage")
            self.assertEqual(json.loads(record["raw_json"])["identifier_coverage"], [
                {"page_index": 0, "complete": complete, "boxes": []},
            ])
            field = json.loads(record["fields_json"])[0]
            if complete:
                self.assert_valid_box(field["bbox"], 0)
            else:
                self.assertIsNone(field["bbox"])

    def test_1099_and_1098_match_the_private_answer_key(self):
        for pdf, fixture in digital_fixtures()[1:]:
            with self.subTest(form=fixture["form_type"]):
                record = extract_digital_pdf(pdf, fixture["sha256"], "run-1")
                self.assertEqual(record["status"], "extracted")
                actual = {
                    field_id(record["doc_id"], field["field_name"], field["occurrence"]):
                    field["extracted_value"]
                    for field in json.loads(record["fields_json"])
                }
                expected = {
                    field["field_id"]: field["expected_value"]
                    for field in fixture["fields"]
                }
                self.assertEqual(actual, expected)

    def test_occurrences_follow_document_order(self):
        pdf = digital_w2(["Synthetic W-2 - 2025", "Wages: 1.00", "Wages: ", "Wages: 2.00"])
        doc_id = hashlib.sha256(pdf).hexdigest()
        fields = json.loads(extract_digital_pdf(pdf, doc_id, "run-1")["fields_json"])
        self.assertEqual(
            [(field["occurrence"], field["extracted_value"]) for field in fields],
            [(1, "1.00"), (2, "2.00")],
        )

    def test_failures_return_the_bronze_shape(self):
        pdf, fixture = bootstrap_fixture()
        samples = (
            (b"", None, "invalid_pdf"),
            (b"garbage", None, "invalid_pdf"),
            (pdf[:-40], None, "invalid_pdf"),
            (pdf, "wrong-hash", "content_hash_mismatch"),
            (digital_w2([]), None, "no_text_layer"),
            (digital_w2(["Unrecognized form", "Wages: 10.00"]), None, "unsupported_form"),
            (digital_w2(["Synthetic W-2 - 2025"]), None, "no_fields"),
        )
        for document, doc_id, error in samples:
            with self.subTest(error=error):
                doc_id = doc_id or hashlib.sha256(document).hexdigest()
                record = extract_digital_pdf(document, doc_id, "run-1")
                self.assertEqual(set(record), BRONZE_COLUMNS)
                self.assertEqual(record["doc_id"], doc_id)
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["error_code"], error)
                self.assertEqual(json.loads(record["fields_json"]), [])
                self.assertIsInstance(json.loads(record["raw_json"]), dict)
        self.assertEqual(fixture["sha256"], hashlib.sha256(pdf).hexdigest())
