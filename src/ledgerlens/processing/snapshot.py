"""Build and check the run-stamped synthetic form snapshot."""

from collections import Counter, defaultdict
from decimal import Decimal
import json
import re

from .replay import AMOUNT_FIELDS, FORM_IDENTIFIERS, IDENTIFIERS, MONEY, amount, fields_for, fingerprint


GRAINS = {
    "silver.fields": ("field_id",),
    "gold.person_year": ("person_key", "tax_year"),
    "gold.missing_documents": ("person_key", "tax_year", "expected_form"),
    "gold.prefill": ("person_key", "tax_year", "form_type", "output_field"),
    "gold.lineage": ("gold_row_id", "output_field", "field_id"),
    "gold.review_summary": ("status",),
    "ops.document_releases": ("doc_id",),
}
DESCRIPTOR = re.compile(r"^Synthetic (W-2|1099|1098) - ([0-9]{4}) - LedgerLens Sample$", re.MULTILINE)
PERSON = re.compile(r"^Fixture person key: ([a-z0-9-]+)$", re.MULTILINE)


def metadata(attempt, inventory):
    text = "\n".join(json.loads(attempt["raw_json"])["pages"])
    forms, people = DESCRIPTOR.findall(text), PERSON.findall(text)
    if len(forms) != 1 or len(people) != 1:
        raise ValueError("Synthetic form metadata is missing or ambiguous")
    form, year_text = forms[0]
    person, year = people[0], int(year_text)
    if (person, year, form) not in inventory:
        raise ValueError("Synthetic form metadata is outside the expected inventory")
    return person, year, form


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

    amounts = defaultdict(list)
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
        person, year, form = metadata(attempt, inventory)
        amount_field = AMOUNT_FIELDS[form]
        document_amounts = []
        for identifier, entry in sorted(fields_for(attempt).items()):
            name, extracted = entry["field_name"], entry["extracted_value"]
            if name not in FORM_IDENTIFIERS[form] and name != amount_field:
                raise ValueError("Extraction field belongs to another form type")
            correction = state.corrections.get(identifier)
            final = correction["corrected_value"] if correction else extracted
            box = entry["bbox"]
            record = add(
                "silver.fields", doc_id=doc_id, field_id=identifier, field_name=name,
                field_type="decimal" if name in MONEY else "string",
                extracted_value=extracted, final_value=final,
                final_decimal=amount(final) if name in MONEY else None,
                final_integer=None, final_date=None,
                value_source="human_correction" if correction else "extraction",
                decision_id=correction["decision_id"] if correction else None,
                confidence=entry["confidence"], bbox_json=json.dumps(box, allow_nan=False)
                if box is not None else None, status=state.status,
                person_key=person, tax_year=year, form_type=form, **provenance(attempt),
            )
            if name in MONEY:
                document_amounts.append(record)
        if state.status == "released":
            if len(document_amounts) != 1 or document_amounts[0]["final_decimal"] is None:
                raise ValueError("Released form must supply one numeric amount")
            amounts[person, year, form].extend(document_amounts)

    def add_lineage(output_id, output_field, sources):
        if not sources or any(source["status"] != "released" for source in sources):
            raise ValueError("Gold value lacks released source fields")
        for source in sources:
            add("gold.lineage", gold_row_id=output_id, output_field=output_field,
                doc_id=source["doc_id"], field_id=source["field_id"],
                decision_id=source["decision_id"], **provenance(source))

    person_years = defaultdict(dict)
    for (person, year, form), sources in sorted(amounts.items()):
        total = sum(source["final_decimal"] for source in sources)
        if total >= Decimal("10000000000000000"):
            raise ValueError("Aggregate amount exceeds DECIMAL(18,2)")
        field_name = AMOUNT_FIELDS[form]
        person_years[person, year][field_name] = total
        person_id = fingerprint(["gold.person_year", person, year])
        prefill_id = fingerprint(["gold.prefill", person, year, form, field_name])
        add("gold.prefill", gold_row_id=prefill_id, person_key=person, tax_year=year,
            form_type=form, output_field=field_name, output_value=str(total))
        add_lineage(person_id, field_name, sources)
        add_lineage(prefill_id, field_name, sources)

    for (person, year), values in sorted(person_years.items()):
        add("gold.person_year", gold_row_id=fingerprint(["gold.person_year", person, year]),
            person_key=person, tax_year=year,
            **{name: values.get(name) for name in AMOUNT_FIELDS.values()})

    for person, year, form in sorted(inventory):
        output_id = fingerprint(["gold.missing_documents", person, year, form])
        sources = amounts.get((person, year, form))
        add("gold.missing_documents", gold_row_id=output_id, person_key=person,
            tax_year=year, expected_form=form, status="present" if sources else "missing")
        if sources:
            add_lineage(output_id, "status", sources)

    for status, count in sorted(Counter(state.status for state in states.values()).items()):
        add("gold.review_summary", status=status, document_count=count)
    return tables
