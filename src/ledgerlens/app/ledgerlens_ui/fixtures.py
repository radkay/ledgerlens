"""Synthetic app data. No dependency on the evaluator's private answer key."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .dashboard import Accuracy, PipelineEvent, Publication

PUBLISHED = Publication("fixture-publication-1", 1, datetime(2025, 11, 23, 16, tzinfo=UTC))


class FixtureReader:
    def latest_publication(self):
        return PUBLISHED

    def document_states(self):
        return ["released", "needs_review", "received"]

    def review_summary(self, run_id):
        return {"released": 1, "needs_review": 1, "rejected": 0} if run_id == PUBLISHED.run_id else {}

    def confidence_scores(self, run_id):
        return [0.98, 0.71, 0.46, None] if run_id == PUBLISHED.run_id else None

    def extraction_accuracy(self, run_id):
        return [Accuracy("W-2", "digital", 4, 3)] if run_id == PUBLISHED.run_id else []


class FixtureBatch:
    """Simulate job events in one authenticated session without launching cloud work."""

    def __init__(self, clock=lambda: datetime.now(UTC)):
        self.clock = clock
        self.requests = {}
        self.runs = {}

    def schedule(self):
        return None

    def run_batch(self, request_id):
        if request_id in self.requests:
            return self.requests[request_id]
        if any((self.clock() - started).total_seconds() < 6 for started in self.runs.values()):
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
