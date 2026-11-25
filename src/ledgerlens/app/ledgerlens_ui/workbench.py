"""Operator review rules shared by the UI and its write backend."""

import re
from dataclasses import dataclass, replace
from datetime import datetime

ACTION_STATES = {
    "approve": {"needs_review", "approved"},
    "correct_field": {"needs_review", "approved"},
    "reject": {"needs_review", "approved"},
    "reprocess": {"needs_review", "rejected", "recalled"},
    "recall": {"released"},
}

IDENTIFIER_FIELD = re.compile(
    r"(^|[^a-z])(ssn|tin|itin|ein|atin|social[^a-z]+security[^a-z]+number|"
    r"taxpayer[^a-z]+identification[^a-z]+number)([^a-z]|$)",
    re.IGNORECASE,
)
IDENTIFIER_VALUE = re.compile(r"(?<!\d)(?:\d{3}-\d{2}-\d{4}|\d{2}-\d{7})(?!\d)")
MASKED = "[masked]"


@dataclass(frozen=True)
class Field:
    field_id: str
    field_name: str
    extracted_value: str | None
    final_value: str | None
    confidence: float | None

    def masked(self):
        if IDENTIFIER_FIELD.search(self.field_name):
            return replace(self, extracted_value=MASKED, final_value=MASKED)
        return replace(
            self, extracted_value=mask_text(self.extracted_value), final_value=mask_text(self.final_value),
        )


@dataclass(frozen=True)
class ReviewDocument:
    doc_id: str
    extraction_id: str | None
    state_at: datetime
    status: str
    fields: tuple[Field, ...] = ()


@dataclass(frozen=True)
class Decision:
    decision_id: str
    doc_id: str
    extraction_id: str | None
    field_id: str | None
    action: str
    reason_code: str
    corrected_value: str | None
    actor_id: str
    decided_at: datetime


def mask_text(value):
    return IDENTIFIER_VALUE.sub(MASKED, value) if value is not None else None


def available_actions(status, reasons):
    return [action for action, states in ACTION_STATES.items() if status in states and reasons.get(action)]


def can_correct(field):
    return not IDENTIFIER_FIELD.search(field.field_name)


def validate_decision(decision, document, reasons, actor_id):
    if decision.actor_id != actor_id:
        raise PermissionError("Decision actor must match the authenticated operator.")
    if (decision.doc_id, decision.extraction_id) != (document.doc_id, document.extraction_id):
        raise ValueError("The current extraction changed. Reload the document.")
    if decision.action not in available_actions(document.status, reasons):
        raise ValueError("This decision is unavailable for the current document state.")
    if decision.reason_code not in reasons[decision.action]:
        raise ValueError("Select an enabled reason for this decision.")
    if decision.action != "correct_field":
        if decision.field_id is not None or decision.corrected_value is not None:
            raise ValueError("Only a field correction can include a field or value.")
        return
    field = next((field for field in document.fields if field.field_id == decision.field_id), None)
    if field is None or not can_correct(field):
        raise ValueError("Select a current field that can be corrected.")
    value = decision.corrected_value
    if not value or not value.strip() or mask_text(value) != value:
        raise ValueError("Enter a value without personal identifiers.")
