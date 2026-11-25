import hashlib
import json
import unittest
from datetime import UTC

from provisioning.fixtures import bootstrap_fixture, digital_w2
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
                "bbox": None,
            }
            for item in fixture["fields"]
        ]
        self.assertEqual(json.loads(record["fields_json"]), expected)
        retry = extract_digital_pdf(pdf, doc_id, "run-1")
        reprocess = extract_digital_pdf(pdf, doc_id, "run-2")
        self.assertEqual(retry["extraction_id"], record["extraction_id"])
        self.assertNotEqual(reprocess["extraction_id"], record["extraction_id"])

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
