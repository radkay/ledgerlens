"""Route a logical extraction through an explicitly ordered adapter plan."""

from collections.abc import Callable
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Stage:
    name: str
    version: str
    extract: Callable[[bytes, str, str], dict]
    fallback_on: frozenset[str] = frozenset()
    require_confidence: bool = False


@dataclass(frozen=True)
class ExtractorPlan:
    stages: tuple[Stage, ...]
    minimum_confidence: float

    def __post_init__(self):
        names = {stage.name for stage in self.stages}
        if not self.stages or len(names) != len(self.stages):
            raise ValueError("An extractor plan needs distinct stages")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("Invalid minimum extraction confidence")

    def configuration(self):
        """Return the ordered configuration for the Processing input digest."""
        return [
            self.minimum_confidence,
            [[stage.name, stage.version, sorted(stage.fallback_on), stage.require_confidence]
             for stage in self.stages],
        ]

    def __call__(self, contents, doc_id, run_id):
        for index, stage in enumerate(self.stages):
            attempt = stage.extract(contents, doc_id, run_id)
            if (attempt["doc_id"], attempt["run_id"], attempt["extractor_name"],
                    attempt["extractor_version"]) != (doc_id, run_id, stage.name, stage.version):
                raise ValueError("Adapter returned an inconsistent extraction identity")
            status = attempt["status"]
            if status == "failed":
                if index + 1 < len(self.stages) and attempt["error_code"] in stage.fallback_on:
                    continue
                return attempt
            if status not in {"extracted", "needs_review"}:
                raise ValueError("Adapter returned an invalid extraction status")
            if self.requires_review(attempt, stage):
                return attempt | {"status": "needs_review"}
            return attempt

    def requires_review(self, attempt, stage):
        entries = json.loads(attempt["fields_json"])
        needs_review = not entries
        for entry in entries:
            confidence = entry["confidence"]
            if confidence is None:
                needs_review |= stage.require_confidence
            elif type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                raise ValueError("Adapter returned invalid field confidence")
            else:
                needs_review |= confidence < self.minimum_confidence
        return needs_review
