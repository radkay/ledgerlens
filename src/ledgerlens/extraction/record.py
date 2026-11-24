"""Bronze attempt records and canonical field identity."""

import hashlib
import json
from datetime import UTC, datetime


def field_id(doc_id: str, field_name: str, occurrence: int) -> str:
    return hashlib.sha256(f"{doc_id}:{field_name}:{occurrence}".encode("utf-8")).hexdigest()


def attempt_record(
    doc_id, run_id, extractor_name, extractor_version, *, raw=None, fields=(), error_code=None
):
    # The Processing job supplies the logical run ID. A reprocess uses a new run ID.
    identity = json.dumps([doc_id, run_id, extractor_name], separators=(",", ":"))
    return {
        "extraction_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "doc_id": doc_id,
        "run_id": run_id,
        "extracted_at": datetime.now(UTC),
        "extractor_name": extractor_name,
        "extractor_version": extractor_version,
        "model_name": None,
        "model_version": None,
        "status": "failed" if error_code else "extracted",
        "raw_json": json.dumps({} if raw is None else raw, allow_nan=False),
        "fields_json": json.dumps(list(fields), allow_nan=False),
        "error_code": error_code,
    }
