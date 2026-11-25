"""Synthetic app data. No dependency on the evaluator's private answer key."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .dashboard import Accuracy, Confidence, PipelineEvent, Publication, confidence_distribution

from .outputs import ExpectedDocument, GoldValue, Lineage, PersonYear, Prefill
from .workbench import Field, ReviewDocument, validate_decision

PUBLISHED = Publication("fixture-publication-1", 1, datetime(2025, 11, 23, 16, tzinfo=UTC))


ASSETS = Path(__file__).with_name("assets")


def fixture_document(pdf, status, wages, confidence):
    doc_id = sha256(pdf).hexdigest()
    fields = tuple(
        Field(sha256(f"{doc_id}:{name}:1".encode()).hexdigest(), name, value, value, score)
        for name, value, score in (
            ("employee_ssn", None, confidence),
            ("employer_ein", None, None),
            ("wages", wages, confidence if status == "released" else 0.71),
        )
    )
    return ReviewDocument(doc_id, f"{doc_id}-extraction-1", PUBLISHED.published_at, status, fields)


RELEASED_PDF = (ASSETS / "released-w2.pdf").read_bytes()
REVIEW_PDF = (ASSETS / "review-w2.pdf").read_bytes()
RELEASED = fixture_document(RELEASED_PDF, "released", "25000.00", 0.98)
NEEDS_REVIEW = fixture_document(REVIEW_PDF, "needs_review", "18750.00", 0.46)
FIXTURE_DOCUMENTS = RELEASED, NEEDS_REVIEW
FIXTURE_PDFS = {RELEASED.doc_id: RELEASED_PDF, NEEDS_REVIEW.doc_id: REVIEW_PDF}
FIXTURE_PREFILL = Prefill("fixture-gold-prefill-1", "fixture-person-001", 2025, "W-2", "wages", "25000.00")
FIXTURE_PERSON_YEAR_ID = "fixture-gold-person-year-1"
FIXTURE_PREFILL_LINEAGE = Lineage(
    FIXTURE_PREFILL.gold_row_id, FIXTURE_PREFILL.output_field, RELEASED.doc_id,
    RELEASED.fields[2].field_id, RELEASED.extraction_id, None, "fixture", "1",
)
FIXTURE_LINEAGE = (
    replace(FIXTURE_PREFILL_LINEAGE, gold_row_id=FIXTURE_PERSON_YEAR_ID),
    FIXTURE_PREFILL_LINEAGE,
)


class FixtureReader:
    def latest_publication(self):
        return PUBLISHED

    def document_states(self):
        return ["released", "needs_review", "received"]

    def review_summary(self, run_id):
        return {"released": 1, "needs_review": 1, "rejected": 0} if run_id == PUBLISHED.run_id else {}

    def confidence_summary(self, run_id):
        if run_id != PUBLISHED.run_id:
            return []
        scores = [field.confidence for field in RELEASED.fields]
        return [Confidence("W-2", bucket, count)
                for bucket, count in confidence_distribution(scores).items() if count]

    def extraction_accuracy(self, run_id):
        return [Accuracy("W-2", "digital", 4, 3)] if run_id == PUBLISHED.run_id else []

    def lineage_documents(self, run_id):
        return [RELEASED.doc_id] if run_id == PUBLISHED.run_id else []

    def lineage_values(self, run_id):
        if run_id != PUBLISHED.run_id:
            return []
        entry = FIXTURE_PREFILL
        values = [
            GoldValue(FIXTURE_PERSON_YEAR_ID, entry.output_field, entry.output_value,
                      "person-year total", entry.person_key, str(entry.tax_year)),
            GoldValue(entry.gold_row_id, entry.output_field, entry.output_value,
                      f"pre-filled {entry.form_type}", entry.person_key, str(entry.tax_year)),
        ]
        for row in self.expected_documents(run_id):
            if row.status == "missing":
                row_id = f"fixture-missing-{row.person_key}-{row.tax_year}-{row.expected_form}"
                values.append(GoldValue(row_id, "status", row.status, f"expected {row.expected_form}",
                                        row.person_key, str(row.tax_year)))
        return values

    def lineage(self, run_id, *, doc_id=None, gold_row_id=None, output_field=None):
        rows = FIXTURE_LINEAGE if run_id == PUBLISHED.run_id else ()
        if doc_id is not None and gold_row_id is None and output_field is None:
            return [row for row in rows if row.doc_id == doc_id]
        if doc_id is None and gold_row_id is not None and output_field is not None:
            return [row for row in rows if (row.gold_row_id, row.output_field) == (gold_row_id, output_field)]
        raise ValueError("Select a source document or one Gold output field.")

    def person_years(self, run_id):
        if run_id != PUBLISHED.run_id:
            return []
        entry = FIXTURE_PREFILL
        return [PersonYear(entry.person_key, entry.tax_year, entry.output_value, None, None)]

    def expected_documents(self, run_id):
        if run_id != PUBLISHED.run_id:
            return []
        entry = FIXTURE_PREFILL
        return [
            ExpectedDocument(entry.person_key, entry.tax_year, entry.form_type, "present"),
            ExpectedDocument(entry.person_key, entry.tax_year, "1099", "missing"),
            ExpectedDocument("fixture-person-002", entry.tax_year, "W-2", "missing"),
        ]

    def prefill_forms(self, run_id):
        return [FIXTURE_PREFILL.form] if run_id == PUBLISHED.run_id else []

    def prefill(self, run_id, form):
        return [FIXTURE_PREFILL] if run_id == PUBLISHED.run_id and form == FIXTURE_PREFILL.form else []


class FixtureBatch:
    """Simulate job events in one authenticated session without launching cloud work."""

    def __init__(self, clock=lambda: datetime.now(UTC)):
        self.clock = clock
        self.requests = {}
        self.runs = {}

    def schedule(self):
        return None

    def active_job_run(self):
        now = self.clock()
        return next((job_run_id for job_run_id, started in self.runs.items()
                     if (now - started).total_seconds() < 6), None)

    def terminal_job_state(self, job_run_id):
        return None

    def run_batch(self, request_id):
        if request_id in self.requests:
            return self.requests[request_id]
        if self.active_job_run():
            raise ValueError("A fixture batch is already running.")
        job_run_id = str(uuid4())
        self.requests[request_id] = job_run_id
        self.runs[job_run_id] = self.clock()
        return job_run_id

    def pipeline_runs(self, job_run_id):
        if job_run_id not in self.runs:
            return []
        started = self.runs[job_run_id]
        events = enumerate(((0, "queued"), (2, "running"), (4, "validated"), (6, "skipped_duplicate")), 1)
        return [
            PipelineEvent(job_run_id, seq, started + timedelta(seconds=delay), status, job_run_id, "manual")
            for seq, (delay, status) in events
            if started + timedelta(seconds=delay) <= self.clock()
        ]


class FixtureWorkbench:
    def __init__(self, actor_id):
        self.actor_id = actor_id
        self._decisions = {}

    def reasons(self):
        return {
            "approve": ("verified",), "correct_field": ("extraction_error",),
            "reject": ("invalid_document",), "reprocess": ("low_confidence",),
            "recall": ("release_correction",),
        }

    def documents(self):
        return tuple(self.document(doc.doc_id) for doc in FIXTURE_DOCUMENTS)

    def document(self, doc_id):
        document = next(doc for doc in FIXTURE_DOCUMENTS if doc.doc_id == doc_id)
        for decision in self._decisions.values():
            if (decision.doc_id, decision.extraction_id) != (doc_id, document.extraction_id):
                continue
            if decision.action == "recall":
                # The published fixture remains visible until a new publication.
                continue
            if decision.action == "correct_field":
                fields = tuple(
                    replace(field, final_value=decision.corrected_value)
                    if field.field_id == decision.field_id else field
                    for field in document.fields
                )
                document = replace(document, status="approved", fields=fields, state_at=decision.decided_at)
            elif decision.action == "reprocess":
                document = replace(document, status="received", extraction_id=None,
                                   fields=(), state_at=decision.decided_at)
            else:
                status = {"approve": "approved", "reject": "rejected"}[decision.action]
                document = replace(document, status=status, state_at=decision.decided_at)
        return document

    def preview_pages(self, document):
        if document.doc_id in FIXTURE_PDFS and document.extraction_id:
            return [0]
        return []

    def preview(self, document, page_index):
        if page_index in self.preview_pages(document):
            return FIXTURE_PDFS[document.doc_id]
        return None

    def append_decision(self, decision, state_at):
        if decision.actor_id != self.actor_id:
            raise PermissionError("Decision actor must match the authenticated operator.")
        existing = self._decisions.get(decision.decision_id)
        if existing is not None:
            if existing != decision:
                raise ValueError("Decision ID already exists with a different payload.")
            return

        document = self.document(decision.doc_id)
        if document.state_at != state_at:
            raise ValueError("The document state changed. Reload the document.")
        validate_decision(decision, document, self.reasons(), self.actor_id)
        self._decisions[decision.decision_id] = decision
