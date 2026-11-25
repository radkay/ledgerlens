"""Reverse trace of one published person-year wage for Processing jobs."""

from decimal import Decimal
import json

from .job import DECISIONS, EXTRACTIONS, PUBLICATIONS, RUNS
from .replay import amount, canonical_decisions, fields_for
from .storage import unique


def trace_wages(store, run_id, gold_row_id):
    """Resolve a published wage to its canonical fields, attempts and corrections."""
    if not store.read(PUBLICATIONS, run_id=run_id):
        raise ValueError("Run has no publication marker")
    outputs = unique(store.read("gold.person_year", run_id=run_id), ("gold_row_id",))
    output = outputs.get((gold_row_id,))
    if output is None or output["wages"] is None:
        raise ValueError("Published Gold wage is unavailable")

    links = unique(
        [row for row in store.read("gold.lineage", run_id=run_id)
         if row["gold_row_id"] == gold_row_id and row["output_field"] == "wages"],
        ("field_id",),
    )
    fields = unique(store.read("silver.fields", run_id=run_id), ("field_id",))
    expected = {
        key for key, field in fields.items()
        if field["status"] == "released" and field["field_name"] == "wages"
        and (field["person_key"], field["tax_year"]) == (output["person_key"], output["tax_year"])
    }
    if links.keys() != expected:
        raise ValueError("Wage lineage does not cover its released Silver fields")
    attempts = unique(store.read(EXTRACTIONS), ("extraction_id",))
    correction_ids = {link["decision_id"] for link in links.values() if link["decision_id"]}
    decisions = {}
    if correction_ids:
        queued, = store.read(RUNS, run_id=run_id, event_status="queued")
        version = json.loads(queued["detail_code"])["versions"][DECISIONS]
        decisions = {
            row["decision_id"]: row for row in canonical_decisions(
                row for row in store.read(DECISIONS, version=version)
                if row["decision_id"] in correction_ids
            )
        }

    sources = []
    for key, link in sorted(links.items()):
        field = fields.get(key)
        attempt = attempts.get((link["extraction_id"],))
        if (
            field is None or attempt is None or field["status"] != "released"
            or field["field_name"] != "wages" or field["final_decimal"] is None
            or (field["person_key"], field["tax_year"]) != (output["person_key"], output["tax_year"])
            or any(field[name] != link[name] for name in (
                "doc_id", "extraction_id", "decision_id", "extractor_name", "extractor_version",
                "model_name", "model_version",
            ))
            or any(attempt[name] != link[name] for name in (
                "doc_id", "extractor_name", "extractor_version", "model_name", "model_version",
            ))
        ):
            raise ValueError("Inconsistent wage lineage")
        original = fields_for(attempt).get(key[0])
        if original is None or original["field_name"] != "wages":
            raise ValueError("Original wage field is unavailable")
        corrected = link["decision_id"]
        final = original["extracted_value"]
        if corrected is not None:
            decision = decisions.get(corrected)
            if decision is None:
                raise ValueError("Wage correction decision is unavailable")
            if (
                decision["action"] != "correct_field"
                or decision["doc_id"] != link["doc_id"] or decision["field_id"] != key[0]
                or decision["extraction_id"] not in (None, link["extraction_id"])
            ):
                raise ValueError("Wage correction targets another source")
            final = decision["corrected_value"]
        if (
            original["extracted_value"] != field["extracted_value"] or final != field["final_value"]
            or amount(final) != field["final_decimal"]
            or field["value_source"] != ("human_correction" if corrected else "extraction")
        ):
            raise ValueError("Wage lineage does not reproduce Silver")
        sources.append(link | {"original_value": original["extracted_value"], "final_wage": amount(final)})

    if not sources or sum((row["final_wage"] for row in sources), Decimal("0.00")) != output["wages"]:
        raise ValueError("Wage lineage does not reproduce Gold")
    return {"run_id": run_id, "gold_row_id": gold_row_id, "wages": output["wages"], "sources": sources}
