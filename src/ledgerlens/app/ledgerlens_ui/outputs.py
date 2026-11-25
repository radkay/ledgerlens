"""Projection of public output rows from one published run."""

from dataclasses import dataclass
import json
import re


LINEAGE_COLUMNS = (
    "output_field", "extractor_name", "extractor_version", "decision_id", "doc_id",
    "field_id", "extraction_id", "gold_row_id", "model_name", "model_version",
)
LINEAGE_IDENTIFIERS = ("decision_id", "doc_id", "field_id", "extraction_id", "gold_row_id")
AMOUNT_FIELDS = {"wages", "other_income", "mortgage_interest"}


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
class GoldValue:
    gold_row_id: str
    output_field: str
    output_value: str | None
    source: str | None
    person_key: str | None
    tax_year: str | None

    @property
    def label(self):
        value = self.output_value if self.output_value is not None else "—"
        return " · ".join(part for part in (self.output_field, value, self.source, self.person_key, self.tax_year)
                          if part is not None)


def default_gold_value_index(values):
    return next((index for index, value in enumerate(values)
                 if value.output_field in AMOUNT_FIELDS and value.output_value is not None), 0)


def lineage_display(rows):
    display = []
    full_identifiers = []
    for link in rows:
        row = {column: getattr(link, column) for column in LINEAGE_COLUMNS}
        full = {}
        for column in LINEAGE_IDENTIFIERS:
            identifier = row[column]
            if identifier and len(identifier) > 12:
                full[column] = identifier
                row[column] = identifier[:12]
        display.append(row)
        full_identifiers.append(full)
    return display, full_identifiers


@dataclass(frozen=True)
class PersonYear:
    person_key: str
    tax_year: int
    wages: str | None
    other_income: str | None
    mortgage_interest: str | None


@dataclass(frozen=True)
class ExpectedDocument:
    person_key: str
    tax_year: int
    expected_form: str
    status: str


@dataclass(frozen=True)
class Form:
    person_key: str
    tax_year: int
    form_type: str

    @property
    def label(self):
        return f"{self.form_type} · {self.tax_year} · {self.person_key}"

    def filename(self, publication):
        label = f"{self.form_type}-{self.tax_year}-{self.person_key}"
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")[:100]
        return f"ledgerlens-{safe_label}-publication-{publication.publication_seq}"


@dataclass(frozen=True)
class Prefill:
    gold_row_id: str
    person_key: str
    tax_year: int
    form_type: str
    output_field: str
    output_value: str | None

    @property
    def form(self):
        return Form(self.person_key, self.tax_year, self.form_type)


def prefill_document(publication, rows):
    first = rows[0]
    return json.dumps({
        "run_id": publication.run_id,
        "publication_seq": publication.publication_seq,
        "published_at": publication.published_at.isoformat(),
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
