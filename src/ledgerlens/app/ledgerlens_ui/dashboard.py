"""Publication-scoped overview and operator-scoped batch status."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

DOCUMENT_STATES = (
    "received", "extracted", "needs_review", "auto_approved",
    "approved", "rejected", "released", "recalled",
)
ACTIVE_BATCH_STATES = frozenset({"queued", "running", "validated"})


@dataclass(frozen=True)
class Publication:
    run_id: str
    publication_seq: int
    published_at: datetime


@dataclass(frozen=True)
class Accuracy:
    form_type: str
    variant: str
    expected_count: int
    exact_match_count: int


@dataclass(frozen=True)
class PipelineEvent:
    run_id: str
    event_seq: int
    event_at: datetime
    status: str
    job_run_id: str
    trigger_type: str
    detail_code: str | None = None


@dataclass(frozen=True)
class BatchActivity:
    job_run_id: str | None
    event: PipelineEvent | None = None
    terminal_job_state: str | None = None

    @property
    def active(self):
        return bool(self.job_run_id and not self.terminal_job_state
                    and (self.event is None or self.event.status in ACTIVE_BATCH_STATES))


@dataclass(frozen=True)
class Schedule:
    expression: str
    timezone: str
    paused: bool


class OverviewReader(Protocol):
    def latest_publication(self) -> Publication | None: ...
    def document_states(self) -> Mapping[str, int] | Sequence[str]: ...
    def review_summary(self, run_id: str) -> dict[str, int]: ...
    def confidence_scores(self, run_id: str) -> list[float | None] | None: ...
    def extraction_accuracy(self, run_id: str) -> list[Accuracy]: ...


class BatchOperator(Protocol):
    def schedule(self) -> Schedule | None: ...
    def active_job_run(self) -> str | None: ...
    def terminal_job_state(self, job_run_id: str) -> str | None: ...
    def run_batch(self, request_id: str) -> str: ...
    def pipeline_runs(self, job_run_id: str) -> list[PipelineEvent]: ...


def confidence_distribution(scores: list[float | None]) -> dict[str, int]:
    counts = dict.fromkeys(("[0, 0.5)", "[0.5, 0.8)", "[0.8, 1]", "Unreported"), 0)
    for score in scores:
        if score is None:
            counts["Unreported"] += 1
        elif score < 0.5:
            counts["[0, 0.5)"] += 1
        elif score < 0.8:
            counts["[0.5, 0.8)"] += 1
        else:
            counts["[0.8, 1]"] += 1
    return counts


def read_overview(reader: OverviewReader):
    publication = reader.latest_publication()
    documents = Counter(reader.document_states())
    live_counts = {status: documents[status] for status in DOCUMENT_STATES}
    if publication is None:
        return publication, live_counts, {}, None, []

    run_id = publication.run_id
    scores = reader.confidence_scores(run_id)
    confidence = confidence_distribution(scores) if scores is not None else None
    return (
        publication, live_counts, reader.review_summary(run_id),
        confidence, reader.extraction_accuracy(run_id),
    )


def latest_batch_event(operator: BatchOperator, job_run_id: str) -> PipelineEvent | None:
    return max(operator.pipeline_runs(job_run_id), key=lambda event: event.event_seq, default=None)


def read_batch_activity(operator: BatchOperator, tracked_run_id: str | None) -> BatchActivity:
    def read(job_run_id):
        event = latest_batch_event(operator, job_run_id)
        job_state = None
        if event is None or event.status in ACTIVE_BATCH_STATES:
            job_state = operator.terminal_job_state(job_run_id)
        return BatchActivity(job_run_id, event, job_state)

    tracked = read(tracked_run_id) if tracked_run_id else BatchActivity(None)
    if tracked.active:
        return tracked
    active_run = operator.active_job_run()
    return read(active_run) if active_run and active_run != tracked_run_id else tracked
