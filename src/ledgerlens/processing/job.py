"""Publish a frozen synthetic digital W-2 snapshot using the final marker."""

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from .replay import canonical_decisions, classify, fingerprint, replay
from .snapshot import GRAINS, build_snapshot, metadata, silver_projection
from .storage import MAX_ROWS, DeltaStore, ensure_rows, unique

DOCUMENTS = "bronze.documents"
EXTRACTIONS = "bronze.extractions"
RUNS = "ops.pipeline_runs"
PUBLICATIONS = "ops.publications"
RELEASES = "ops.document_releases"
INVENTORY = "bronze.expected_forms"
DECISIONS = "review.decisions"
REASONS = "review.reason_codes"
INPUTS = (INVENTORY, REASONS, DECISIONS, DOCUMENTS, EXTRACTIONS, PUBLICATIONS, RELEASES)
WRITES = (DOCUMENTS, EXTRACTIONS, RUNS, PUBLICATIONS, *GRAINS)
TRIGGER_TYPES = {"one_time": "manual", "periodic": "scheduled"}
PDF = re.compile(r"([a-f0-9]{64})\.pdf")
PROCESSING_REVISION = 1


class Inbox:
    def __init__(self, catalog):
        self.root = Path(f"/Volumes/{catalog}/bronze/inbox")

    def files(self):
        return [p.name for p in self.root.iterdir() if p.suffix.lower() == ".pdf"]

    def read(self, doc_id):
        path = self.root / f"{doc_id}.pdf"
        with path.open("rb") as file:
            contents = file.read(5_000_001)
        if len(contents) > 5_000_000:
            raise ValueError("Synthetic PDF exceeds the byte limit")
        return str(path), contents


def capture(store, inbox, extractor_version):
    filenames = sorted(set(inbox.files()))
    if len(filenames) > MAX_ROWS or any(not PDF.fullmatch(name) for name in filenames):
        raise ValueError("Unexpected inbox PDF name or count")
    frozen = {
        "versions": {table: store.version(table) for table in INPUTS},
        "documents": [PDF.fullmatch(name)[1] for name in filenames],
        "configuration": [PROCESSING_REVISION, "pypdf", extractor_version],
    }
    data = read_frozen(store, frozen)
    digest = fingerprint([
        frozen["documents"], frozen["configuration"],
        *[sorted({fingerprint(row) for row in data[table]})
          for table in (INVENTORY, REASONS, DECISIONS)],
    ])
    return frozen, data, digest


def read_frozen(store, frozen):
    return {table: store.read(table, version=frozen["versions"][table]) for table in INPUTS}


def latest_publication(rows):
    ordered = sorted(rows, key=lambda row: row["publication_seq"])
    unique(ordered, ("run_id",))
    unique(ordered, ("publication_seq",))
    return ordered[-1] if ordered else None


def load_documents(store, inbox, extract, frozen, data, run_id, queued_at):
    documents = unique(data[DOCUMENTS], ("doc_id",))
    unique(data[DOCUMENTS], ("content_sha256",))
    for (doc_id,), row in documents.items():
        if doc_id != row["content_sha256"]:
            raise ValueError("Document identity differs from content hash")
    unique(data[EXTRACTIONS], ("extraction_id",))
    unique(data[RELEASES], ("run_id", "doc_id"))
    inventory = set(unique(data[INVENTORY], ("person_key", "tax_year", "expected_form")))
    reasons = {key for key, row in unique(data[REASONS], ("action", "reason_code")).items()
               if row["enabled"]}
    decisions = canonical_decisions(data[DECISIONS])
    states = replay(documents.values(), data[EXTRACTIONS], decisions,
                    data[PUBLICATIONS], data[RELEASES], reasons)
    timestamp = max(
        [queued_at] + [row[key] + timedelta(microseconds=1)
                       for table, key in ((DOCUMENTS, "received_at"), (DECISIONS, "decided_at"),
                                          (EXTRACTIONS, "extracted_at"), (PUBLICATIONS, "published_at"))
                       for row in data[table]],
    )
    new_documents = []
    attempts = []
    failed = False
    for doc_id in sorted(set(frozen["documents"]) | states.keys()):
        if doc_id in states and states[doc_id].status != "received":
            continue
        path, contents = inbox.read(doc_id)
        if sha256(contents).hexdigest() != doc_id:
            raise ValueError("Inbox content differs from frozen hash")
        if doc_id not in states:
            new_documents.append({
                "doc_id": doc_id, "content_sha256": doc_id, "file_path": path,
                "received_at": timestamp, "run_id": run_id, "status": "received",
            })
        attempt = extract(contents, doc_id, run_id)
        if (attempt["doc_id"] != doc_id or attempt["run_id"] != run_id
                or [attempt["extractor_name"], attempt["extractor_version"]]
                != frozen["configuration"][1:]):
            raise ValueError("Extractor returned inconsistent identity")
        attempt["extracted_at"] = timestamp
        if attempt["status"] == "extracted":
            metadata(attempt, inventory)
            attempt["status"] = classify(attempt)
        elif attempt["status"] != "failed":
            raise ValueError("Unexpected digital extraction status")
        failed |= attempt["status"] == "failed"
        attempts.append(attempt)
    ensure_rows(store, DOCUMENTS, new_documents, ("doc_id",))
    ensure_rows(store, EXTRACTIONS, attempts, ("extraction_id",))
    if failed:
        raise ValueError("Digital extraction failed")
    return (
        replay([*documents.values(), *new_documents], [*data[EXTRACTIONS], *attempts],
               decisions, data[PUBLICATIONS], data[RELEASES], reasons),
        inventory, timestamp,
    )


def publish(store, inbox, extract, *, extractor_version, job_run_id, trigger_type,
            clock=lambda: datetime.now(UTC)):
    """Run under the serialized Processing job, with one durable freeze per logical run."""
    if not job_run_id or trigger_type not in TRIGGER_TYPES.values():
        raise ValueError("Invalid job invocation")
    events = store.read(RUNS)
    current = latest_publication(store.read(PUBLICATIONS))
    queued_by_id = {row["run_id"]: row for row in events if row["status"] == "queued"}
    prior_invocation = next((row for row in reversed(sorted(events, key=lambda row: row["event_at"]))
                             if row["job_run_id"] == job_run_id and row["run_id"] in queued_by_id
                             and row["status"] != "skipped_duplicate"), None)

    def event(status, detail=None):
        sequence = max((row["event_seq"] for row in events if row["run_id"] == run_id), default=0)
        row = {
            "run_id": run_id, "event_seq": sequence + 1, "event_at": clock(),
            "status": status, "detail_code": detail, "job_run_id": job_run_id,
            "trigger_type": trigger_type,
        }
        store.append(RUNS, [row])
        events.append(row)
        return row

    if prior_invocation is not None:
        run_id = prior_invocation["run_id"]
        queued = queued_by_id[run_id]
        frozen = json.loads(queued["detail_code"])
        digest = frozen["input_digest"]
        if any(row["run_id"] == run_id for row in store.read(PUBLICATIONS)):
            if not any(row["run_id"] == run_id and row["status"] == "published" for row in events):
                event("published")
            event("skipped_duplicate")
            return run_id
        if frozen["predecessor"] != (current["run_id"] if current else None):
            event("failed", "superseded")
            raise ValueError("Frozen run was superseded by another publication")
    else:
        frozen, data, digest = capture(store, inbox, extractor_version)
        current = latest_publication(data[PUBLICATIONS])
        if current and current["input_digest"] == digest:
            run_id = current["run_id"]
            if not any(row["run_id"] == run_id and row["status"] == "published" for row in events):
                event("published")
            event("skipped_duplicate")
            return run_id
        predecessor = current["run_id"] if current else None
        run_id = fingerprint([predecessor, digest])
        queued = queued_by_id.get(run_id)
        if queued is None:
            frozen.update(input_digest=digest, predecessor=predecessor)
            queued = event("queued", json.dumps(frozen, sort_keys=True, separators=(",", ":")))
        else:
            frozen = json.loads(queued["detail_code"])

    phase = "frozen_inputs"
    try:
        event("running")
        if frozen["configuration"] != [PROCESSING_REVISION, "pypdf", extractor_version]:
            raise ValueError("Frozen processor configuration is unavailable")
        data = read_frozen(store, frozen)
        if not frozen["documents"] and not data[DOCUMENTS]:
            raise ValueError("No received synthetic documents")
        phase = "bronze_processing"
        states, inventory, timestamp = load_documents(
            store, inbox, extract, frozen, data, run_id, queued["event_at"],
        )
        phase = "snapshot_validation"
        tables = build_snapshot(run_id, states, inventory)
        for table, rows in tables.items():
            projection = {"silver.fields": silver_projection}.get(table)
            options = {"projection": projection} if projection else {}
            ensure_rows(store, table, rows, GRAINS[table], run_id=run_id, **options)
        event("validated")
        phase = "publication_marker"
        store.append(PUBLICATIONS, [{
            "publication_seq": 1 if current is None else current["publication_seq"] + 1,
            "run_id": run_id,
            "published_at": max(clock(), timestamp + timedelta(microseconds=1)),
            "input_digest": digest,
        }])
    except Exception:
        if not store.read(PUBLICATIONS, run_id=run_id):
            event("failed", phase)
            raise
    event("published")
    return run_id


def run(spark, catalog, job_run_id, trigger):
    from pypdf import __version__
    from ..extraction import extract_digital_pdf

    store = DeltaStore(spark, catalog, readable=INPUTS, writable=WRITES)
    try:
        return publish(store, Inbox(catalog), extract_digital_pdf, extractor_version=__version__,
                       job_run_id=job_run_id, trigger_type=TRIGGER_TYPES[trigger])
    except Exception as error:
        # Connector exception messages can include input values. Report the error class only.
        error_class = getattr(error, "getErrorClass", lambda: None)() or type(error).__name__
        raise RuntimeError(f"Processing job failed ({error_class})") from None
