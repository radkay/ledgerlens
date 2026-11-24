"""Build and check the run-stamped digital W-2 snapshot."""

from collections import Counter, defaultdict
from decimal import Decimal
import json
import re

from .replay import IDENTIFIERS, amount, fields_for, fingerprint


GRAINS = {
    "silver.fields": ("field_id",),
    "gold.person_year": ("person_key", "tax_year"),
    "gold.missing_documents": ("person_key", "tax_year", "expected_form"),
    "gold.prefill": ("person_key", "tax_year", "form_type", "output_field"),
    "gold.lineage": ("gold_row_id", "output_field", "field_id"),
    "gold.review_summary": ("status",),
    "ops.document_releases": ("doc_id",),
}
DESCRIPTOR = re.compile(r"^Synthetic W-2 - ([0-9]{4}) - LedgerLens Sample$", re.MULTILINE)
PERSON = re.compile(r"^Fixture person key: ([a-z0-9-]+)$", re.MULTILINE)


def metadata(attempt, inventory):
    text = "\n".join(json.loads(attempt["raw_json"])["pages"])
    years, people = DESCRIPTOR.findall(text), PERSON.findall(text)
    if len(years) != 1 or len(people) != 1:
        raise ValueError("Digital W-2 metadata is missing or ambiguous")
    person, year = people[0], int(years[0])
    if (person, year, "W-2") not in inventory:
        raise ValueError("Digital W-2 metadata is outside the expected inventory")
    return person, year


def provenance(attempt):
    return {key: attempt[key] for key in (
        "extraction_id", "extractor_name", "extractor_version", "model_name", "model_version",
    )}


def silver_projection(row):
    if row["field_name"] in IDENTIFIERS:
        return row | {"extracted_value": None, "final_value": None}
    return row


def build_snapshot(run_id, states, inventory):
    tables = {table: [] for table in GRAINS}

    def add(table, **row):
        record = {"run_id": run_id, **row}
        tables[table].append(record)
        return record

    wages = defaultdict(list)
    for doc_id, state in sorted(states.items()):
        attempt = state.attempt
        if state.recall_pending or state.status in {"approved", "auto_approved"}:
            marker = add(
                "ops.document_releases", doc_id=doc_id, extraction_id=attempt["extraction_id"],
                status="recalled" if state.recall_pending else "released",
            )
            state.release(marker)
        if attempt is None or attempt["status"] == "failed":
            if state.status == "released":
                raise ValueError("Released document lacks a successful extraction")
            continue
        person, year = metadata(attempt, inventory)
        document_wages = []
        for identifier, entry in sorted(fields_for(attempt).items()):
            name, extracted = entry["field_name"], entry["extracted_value"]
            correction = state.corrections.get(identifier)
            final = correction["corrected_value"] if correction else extracted
            box = entry["bbox"]
            record = add(
                "silver.fields", doc_id=doc_id, field_id=identifier, field_name=name,
                field_type="decimal" if name == "wages" else "string",
                extracted_value=extracted, final_value=final,
                final_decimal=amount(final) if name == "wages" else None,
                final_integer=None, final_date=None,
                value_source="human_correction" if correction else "extraction",
                decision_id=correction["decision_id"] if correction else None,
                confidence=entry["confidence"], bbox_json=json.dumps(box, allow_nan=False)
                if box is not None else None, status=state.status,
                person_key=person, tax_year=year, form_type="W-2", **provenance(attempt),
            )
            if name == "wages":
                document_wages.append(record)
        if state.status == "released":
            if len(document_wages) != 1 or document_wages[0]["final_decimal"] is None:
                raise ValueError("Released W-2 must supply one numeric wage")
            wages[person, year].extend(document_wages)

    def add_lineage(output_id, output_field, sources):
        if not sources or any(source["status"] != "released" for source in sources):
            raise ValueError("Gold value lacks released source fields")
        for source in sources:
            add("gold.lineage", gold_row_id=output_id, output_field=output_field,
                doc_id=source["doc_id"], field_id=source["field_id"],
                decision_id=source["decision_id"], **provenance(source))

    for (person, year), sources in sorted(wages.items()):
        total = sum(source["final_decimal"] for source in sources)
        if total >= Decimal("10000000000000000"):
            raise ValueError("Aggregate wages exceed DECIMAL(18,2)")
        person_id = fingerprint(["gold.person_year", person, year])
        prefill_id = fingerprint(["gold.prefill", person, year, "W-2", "wages"])
        add("gold.person_year", gold_row_id=person_id, person_key=person, tax_year=year,
            wages=total, other_income=None, mortgage_interest=None)
        add("gold.prefill", gold_row_id=prefill_id, person_key=person, tax_year=year,
            form_type="W-2", output_field="wages", output_value=str(total))
        add_lineage(person_id, "wages", sources)
        add_lineage(prefill_id, "wages", sources)

    for person, year, form in sorted(inventory):
        output_id = fingerprint(["gold.missing_documents", person, year, form])
        sources = wages.get((person, year)) if form == "W-2" else None
        add("gold.missing_documents", gold_row_id=output_id, person_key=person,
            tax_year=year, expected_form=form, status="present" if sources else "missing")
        if sources:
            add_lineage(output_id, "status", sources)

    for status, count in sorted(Counter(state.status for state in states.values()).items()):
        add("gold.review_summary", status=status, document_count=count)
    return tables
