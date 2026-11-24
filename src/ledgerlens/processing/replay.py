"""Deterministic document state and extraction-scoped review decisions."""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
import re


IDENTIFIERS = {"employee_ssn", "employer_ein"}
WAGE = re.compile(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?", re.ASCII)
ACTIONS = {
    "approve": {"needs_review", "approved"},
    "correct_field": {"needs_review", "approved"},
    "reject": {"needs_review", "approved"},
    "reprocess": {"needs_review", "rejected", "recalled"},
    "recall": {"released"},
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=lambda item: item.isoformat(),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def field_id(doc_id, name, occurrence):
    return hashlib.sha256(f"{doc_id}:{name}:{occurrence}".encode("utf-8")).hexdigest()


def amount(value):
    if isinstance(value, str) and WAGE.fullmatch(value):
        return Decimal(value).quantize(Decimal("0.01"))
    return None


def fields_for(attempt):
    fields = {}
    for entry in json.loads(attempt["fields_json"]):
        name, occurrence = entry["field_name"], entry["occurrence"]
        if name not in IDENTIFIERS | {"wages"} or type(occurrence) is not int or occurrence < 1:
            raise ValueError("Invalid digital W-2 field")
        identifier = field_id(attempt["doc_id"], name, occurrence)
        if identifier in fields:
            raise ValueError("Duplicate extraction field")
        fields[identifier] = entry
    return fields


def classify(attempt):
    fields = list(fields_for(attempt).values())
    if (len(fields) == 3 and {item["field_name"] for item in fields} == IDENTIFIERS | {"wages"}
            and all(item["occurrence"] == 1 for item in fields)
            and all(
                amount(item["extracted_value"]) is not None if item["field_name"] == "wages"
                else isinstance(item["extracted_value"], str) and bool(item["extracted_value"])
                for item in fields
            )):
        return "auto_approved"
    return "needs_review"


def canonical_decisions(rows):
    decisions = {}
    for row in rows:
        previous = decisions.setdefault(row["decision_id"], row)
        if previous != row:
            raise ValueError("Conflicting duplicate decision")
    return sorted(decisions.values(), key=lambda row: (row["decided_at"], row["decision_id"]))


@dataclass
class Document:
    status: str = "received"
    attempt: dict | None = None
    corrections: dict = field(default_factory=dict)
    recall_pending: bool = False

    def extract(self, attempt):
        self.attempt = attempt
        self.status = "needs_review" if attempt["status"] == "failed" else attempt["status"]
        self.corrections.clear()
        self.recall_pending = False

    def decide(self, row, reasons):
        action = row["action"]
        current = self.attempt["extraction_id"] if self.attempt else None
        if row["extraction_id"] not in (None, current) or self.status not in ACTIONS.get(action, ()):
            return
        if (action, row["reason_code"]) not in reasons:
            raise ValueError("Decision reason is unavailable")
        if action == "correct_field":
            fields = fields_for(self.attempt)
            entry = fields.get(row["field_id"])
            value = row["corrected_value"]
            if (entry is None or not isinstance(value, str) or not value.strip()
                    or (entry["field_name"] == "wages" and amount(value) is None)):
                raise ValueError("Invalid field correction")
            self.corrections[row["field_id"]] = row
        elif row["field_id"] is not None or row["corrected_value"] is not None:
            raise ValueError("Unexpected decision field or value")
        if action in {"approve", "correct_field"}:
            self.status = "approved"
        elif action == "reject":
            self.status = "rejected"
        elif action == "reprocess":
            self.status = "received"
            self.attempt = None
            self.corrections.clear()
        else:
            self.recall_pending = True

    def release(self, marker):
        if not self.attempt or marker["extraction_id"] != self.attempt["extraction_id"]:
            raise ValueError("Release marker targets a stale extraction")
        target = marker["status"]
        if not (
            target == "released" and self.status in {"approved", "auto_approved", "released"}
            or target == "recalled" and self.status == "released"
        ):
            raise ValueError("Invalid release transition")
        self.status = target
        self.recall_pending = False


def replay(documents, attempts, decisions, publications, releases, reasons):
    states = {row["doc_id"]: Document() for row in documents}
    published = {row["run_id"]: row for row in publications}
    events = [
        (row["extracted_at"], 1, row["extraction_id"], row) for row in attempts
    ] + [
        (row["decided_at"], 2, row["decision_id"], row) for row in decisions
    ] + [
        (published[row["run_id"]]["published_at"], 3,
         published[row["run_id"]]["publication_seq"], row)
        for row in releases if row["run_id"] in published
    ]
    for _, kind, _, row in sorted(events, key=lambda event: event[:3]):
        state = states.get(row["doc_id"])
        if state is None:
            if kind == 2:
                continue
            raise ValueError("Event lacks a registered document")
        if kind == 1:
            state.extract(row)
        elif kind == 2:
            state.decide(row, reasons)
        else:
            state.release(row)
    return states
