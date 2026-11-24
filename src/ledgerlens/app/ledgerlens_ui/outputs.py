"""Projection of public output rows from one published run."""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Lineage:
    gold_row_id: str
    output_field: str
    doc_id: str
    field_id: str
    extraction_id: str
    decision_id: str | None
    extractor_name: str
    extractor_version: str
    model_name: str | None = None
    model_version: str | None = None


@dataclass(frozen=True)
class Prefill:
    gold_row_id: str
    person_key: str
    tax_year: int
    form_type: str
    output_field: str
    output_value: str | None


def prefill_document(publication, rows):
    first = rows[0]
    return json.dumps({
        "run_id": publication.run_id,
        "publication_seq": publication.publication_seq,
        "person_key": first.person_key,
        "tax_year": first.tax_year,
        "form_type": first.form_type,
        "fields": {row.output_field: row.output_value for row in rows},
    }, indent=2)


def prefill_pdf(publication, rows):
    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    output = BytesIO()
    form = rows[0]
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Synthetic {escape(form.form_type)} pre-fill", styles["Title"]),
        Paragraph(f"{escape(form.person_key)} · {form.tax_year}", styles["Normal"]),
        Paragraph(f"Publication {publication.publication_seq} · {escape(publication.run_id)}", styles["Normal"]),
        Spacer(1, 16),
    ]
    for row in rows:
        name = escape(row.output_field)
        value = escape(row.output_value if row.output_value is not None else "—")
        story.append(Paragraph(f"<b>{name}</b> · {value}", styles["Normal"]))
    SimpleDocTemplate(output, pagesize=letter, title="LedgerLens pre-filled form").build(story)
    return output.getvalue()
