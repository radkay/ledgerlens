"""Adapt Databricks document parsing to the Bronze extraction contract."""

import hashlib
import json
from io import BytesIO
from math import isfinite

from .fields import document_form, extract_fields
from .record import attempt_record

_ADAPTER_VERSION = "1"
_PARSE_SQL = """
    SELECT to_json(ai_parse_document(
        :document, map('version', '2.0', 'descriptionElementTypes', '')
    )) AS parsed_json
"""
_TEXT_ELEMENT_TYPES = {"title", "text", "section_header", "page_header"}


def _finite_float(text):
    value = float(text)
    if not isfinite(value):
        raise ValueError("Non-finite JSON number")
    return value


class AiParseDocumentExtractor:
    """Use the Processing job's explicitly selected Spark session."""

    def __init__(self, spark):
        self._spark = spark

    def __call__(self, document: bytes, doc_id: str, run_id: str) -> dict:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        def record(**result):
            return attempt_record(doc_id, run_id, "ai_parse_document", _ADAPTER_VERSION, **result)

        if hashlib.sha256(document).hexdigest() != doc_id:
            return record(error_code="content_hash_mismatch")
        try:
            reader = PdfReader(BytesIO(document), strict=True)
            if reader.is_encrypted:
                return record(error_code="encrypted_pdf")
            if not reader.pages:
                return record(error_code="invalid_pdf")
        except (PyPdfError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            return record(error_code="invalid_pdf")

        # Binding the bytes keeps the SQL statement constant. Spark service failures
        # propagate to the Processing job for retry.
        row = self._spark.sql(_PARSE_SQL, args={"document": bytearray(document)}).first()
        try:
            parsed = json.loads(
                row["parsed_json"], parse_float=_finite_float, parse_constant=_finite_float,
            )
            if not isinstance(parsed, dict):
                return record(error_code="invalid_parse_result")
            errors = parsed.get("error_status")
            if errors:
                return record(raw=parsed, error_code="document_parse_error")
            elements = parsed["document"]["elements"]
            if not isinstance(elements, list):
                return record(error_code="invalid_parse_result")
            text = "\n".join(
                element["content"]
                for element in sorted(elements, key=lambda element: element["id"])
                if element["type"] in _TEXT_ELEMENT_TYPES
            )
        except (KeyError, TypeError, ValueError):
            return record(error_code="invalid_parse_result")

        form_type = document_form(text)
        if form_type is None:
            return record(raw=parsed, error_code="unsupported_form")
        # Databricks reports confidence and pixel boxes at the element level.
        # The field entries keep these values null when exact field data is unavailable.
        fields = extract_fields(text, form_type)
        if not fields:
            return record(raw=parsed, error_code="no_fields")
        return record(raw=parsed, fields=fields)
