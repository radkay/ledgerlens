import hashlib
import json
import unittest
from unittest.mock import Mock

from provisioning.fixtures import bootstrap_fixture, digital_fixtures, scanned_fixtures
from src.ledgerlens.extraction import AiParseDocumentExtractor, extract_digital_pdf


class AiParseDocumentTests(unittest.TestCase):
    def test_document_elements_use_the_digital_adapter_field_contract(self):
        for pdf, fixture in digital_fixtures():
            with self.subTest(form=fixture["form_type"]):
                spark = Mock()
                expected = extract_digital_pdf(pdf, fixture["sha256"], "run-1")
                lines = json.loads(expected["raw_json"])["pages"][0].splitlines()
                elements = [
                    {
                        "id": index,
                        "type": "title" if index == 0 else "text",
                        "content": line,
                        "confidence": 0.98,
                        "bbox": [{"coord": [12, 20, 90, 50], "page_id": 0}],
                    }
                    for index, line in enumerate(lines)
                ]
                parsed = {
                    "document": {"pages": [{"id": 0}], "elements": elements[::-1]},
                    "error_status": [],
                    "metadata": {"version": "2.0"},
                }
                spark.sql.return_value.first.return_value = {"parsed_json": json.dumps(parsed)}
                extract = AiParseDocumentExtractor(spark)
                record = extract(pdf, fixture["sha256"], "run-1")
                self.assertEqual(set(record), set(expected))
                self.assertEqual(record["status"], "extracted")
                fields = json.loads(expected["fields_json"])
                expected_fields = [field | {"bbox": None} for field in fields]
                self.assertEqual(json.loads(record["fields_json"]), expected_fields)
                self.assertEqual(json.loads(record["raw_json"]), parsed | {
                    "identifier_coverage": [{"page_index": 0, "complete": False, "boxes": []}],
                })
                self.assertEqual(record["extractor_name"], "ai_parse_document")
                self.assertIsNone(record["model_name"])
                self.assertIsNone(record["model_version"])
                self.assertNotEqual(record["extraction_id"], expected["extraction_id"])
                statement, = spark.sql.call_args.args
                self.assertIn("ai_parse_document(", statement)
                self.assertIn(":document", statement)
                self.assertIn("'version', '2.0'", statement)
                self.assertIn("'descriptionElementTypes', ''", statement)
                self.assertEqual(spark.sql.call_args.kwargs, {"args": {"document": bytearray(pdf)}})

    def test_raster_input_uses_the_managed_response(self):
        pdf, fixture = scanned_fixtures()[0]
        spark = Mock()
        spark.sql.return_value.first.return_value = {"parsed_json": json.dumps({
            "document": {"elements": [
                {"id": 0, "type": "title", "content": "Synthetic W-2 - 2025 - LedgerLens Sample"},
                {"id": 1, "type": "text", "content": "Wages: 25000.00"},
            ]},
        })}
        record = AiParseDocumentExtractor(spark)(pdf, fixture["sha256"], "run-1")
        self.assertEqual(record["status"], "extracted")
        self.assertEqual(json.loads(record["fields_json"]), [{
            "field_name": "wages", "occurrence": 1, "extracted_value": "25000.00",
            "confidence": None, "bbox": None,
        }])

    def test_invalid_pdf_and_hash_mismatch_do_not_call_spark(self):
        bad_pdf = b"bad"
        spark = Mock()
        extract = AiParseDocumentExtractor(spark)
        mismatch = extract(bad_pdf, "mismatch", "run-1")
        malformed = extract(bad_pdf, hashlib.sha256(bad_pdf).hexdigest(), "run-1")
        self.assertEqual(mismatch["error_code"], "content_hash_mismatch")
        self.assertEqual(malformed["error_code"], "invalid_pdf")
        self.assertEqual(malformed["status"], "failed")
        spark.sql.assert_not_called()

    def test_invalid_result_and_service_reported_page_errors_fail_the_attempt(self):
        pdf, fixture = bootstrap_fixture()
        spark = Mock()
        extract = AiParseDocumentExtractor(spark)
        samples = (
            ("not JSON", "invalid_parse_result"),
            ("null", "invalid_parse_result"),
            ('{"document": {"elements": []}, "metadata": {"confidence": NaN}}',
             "invalid_parse_result"),
            ('{"document": {"elements": []}, "metadata": {"confidence": 1e999}}',
             "invalid_parse_result"),
            ('{"document": {"elements": null}}', "invalid_parse_result"),
            ('{"error_status": [{"error_message": "Example page failure", "page_id": 0}]}',
             "document_parse_error"),
        )
        for response, error in samples:
            with self.subTest(error=error):
                spark.sql.return_value.first.return_value = {"parsed_json": response}
                record = extract(pdf, fixture["sha256"], "run-1")
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["error_code"], error)
                self.assertEqual(json.loads(record["fields_json"]), [])
                self.assertEqual(json.loads(record["raw_json"])["identifier_coverage"], [
                    {"page_index": 0, "complete": False, "boxes": []},
                ])

    def test_spark_failure_reaches_the_processing_job(self):
        pdf, fixture = bootstrap_fixture()
        spark = Mock()
        spark.sql.side_effect = RuntimeError("Example service failure")
        with self.assertRaisesRegex(RuntimeError, "Example service failure"):
            AiParseDocumentExtractor(spark)(pdf, fixture["sha256"], "run-1")
