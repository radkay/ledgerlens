"""Extract the text layer from synthetic digital PDFs using pypdf."""

import hashlib
from io import BytesIO

from .fields import document_form, extract_fields
from .record import attempt_record


def extract_digital_pdf(document: bytes, doc_id: str, run_id: str) -> dict:
    """Return the Bronze attempt for one PDF in a logical Processing job run."""
    from pypdf import PdfReader, __version__
    from pypdf.errors import PyPdfError

    def record(**result):
        return attempt_record(doc_id, run_id, "pypdf", __version__, **result)

    if hashlib.sha256(document).hexdigest() != doc_id:
        return record(error_code="content_hash_mismatch")

    try:
        reader = PdfReader(BytesIO(document), strict=True)
        if reader.is_encrypted:
            return record(error_code="encrypted_pdf")
        pages = [page.extract_text(extraction_mode="plain") for page in reader.pages]
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return record(error_code="invalid_pdf")

    text = "\n".join(pages)
    raw = {"pages": pages}
    if not text.strip():
        return record(raw=raw, error_code="no_text_layer")

    form_type = document_form(text)
    if form_type is None:
        return record(raw=raw, error_code="unsupported_form")

    fields = extract_fields(text, form_type)
    if not fields:
        return record(raw=raw, error_code="no_fields")
    return record(raw=raw, fields=fields)
