"""Write aggregate extraction accuracy under the evaluator identity."""

from collections import defaultdict
import json

from ..processing.job import DOCUMENTS, EXTRACTIONS, PUBLICATIONS, latest_publication
from ..processing.replay import field_id
from ..processing.storage import DeltaStore, ensure_rows, unique

ANSWER_KEY = "eval.answer_key"
ACCURACY = "gold.extraction_accuracy"
READS = (ANSWER_KEY, DOCUMENTS, EXTRACTIONS, PUBLICATIONS)


def score(publication, documents, extractions, answer_key):
    """Compare the first Bronze attempt with each seeded fixture field."""
    cutoff = publication["published_at"]
    received = unique([row for row in documents if row["received_at"] <= cutoff], ("doc_id",))
    attempts = [row for row in extractions
                if row["extracted_at"] <= cutoff and (row["doc_id"],) in received]
    unique(attempts, ("extraction_id",))
    originals = {}
    for row in sorted(attempts, key=lambda row: (row["extracted_at"], row["extraction_id"])):
        originals.setdefault(row["doc_id"], row)

    extracted = {}
    for doc_id, original in originals.items():
        values = {}
        if original["status"] != "failed":
            for entry in json.loads(original["fields_json"]):
                identifier = field_id(doc_id, entry["field_name"], entry["occurrence"])
                if identifier in values:
                    raise ValueError("Duplicate field in original extraction")
                values[identifier] = entry["extracted_value"]
        extracted[doc_id] = values

    if not answer_key:
        raise ValueError("No expected fixture fields")
    unique(answer_key, ("fixture_id", "field_id"))
    aggregates = defaultdict(lambda: [0, 0])
    for row in answer_key:
        group = (row["form_type"], row["variant"])
        if group[0] not in {"W-2", "1099", "1098"} or group[1] not in {"digital", "scanned"}:
            raise ValueError("Unexpected fixture form or variant")
        counts = aggregates[group]
        counts[0] += 1
        actual = extracted.get(row["doc_id"], {}).get(row["field_id"])
        counts[1] += actual is not None and actual == row["expected_value"]
    return [
        {"run_id": publication["run_id"], "form_type": form, "variant": variant,
         "expected_count": expected_count, "exact_match_count": exact_match_count}
        for (form, variant), (expected_count, exact_match_count) in sorted(aggregates.items())
    ]


def evaluate(store):
    publication = latest_publication(store.read(PUBLICATIONS))
    if publication is None:
        return None
    rows = score(publication, store.read(DOCUMENTS), store.read(EXTRACTIONS), store.read(ANSWER_KEY))
    run_id = publication["run_id"]
    ensure_rows(store, ACCURACY, rows, ("form_type", "variant"), run_id=run_id)
    return run_id


def run(spark, catalog):
    try:
        store = DeltaStore(spark, catalog, readable=READS, writable=(ACCURACY,))
        return evaluate(store)
    except Exception as error:
        error_class = getattr(error, "getErrorClass", lambda: None)() or type(error).__name__
        raise RuntimeError(f"Evaluation job failed ({error_class})") from None
