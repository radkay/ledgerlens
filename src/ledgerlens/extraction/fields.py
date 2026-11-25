"""Map synthetic form labels to canonical field entries."""

import re
from collections import Counter

FORM_FIELDS = {
    "W-2": {
        "Employee SSN": "employee_ssn",
        "Employer EIN": "employer_ein",
        "Wages": "wages",
    },
    "1099": {
        "Recipient SSN": "recipient_ssn",
        "Payer TIN": "payer_tin",
        "Other income": "other_income",
    },
    "1098": {
        "Borrower SSN": "borrower_ssn",
        "Lender TIN": "lender_tin",
        "Mortgage interest": "mortgage_interest",
    },
}
FORM_HEADER = re.compile(r"^Synthetic (W-2|1099|1098)\s+-\s+\d{4}\b")


def document_form(text: str) -> str | None:
    for line in text.splitlines():
        header = FORM_HEADER.match(line.strip())
        if header:
            return header[1]
    return None


def extract_fields(text: str, form_type: str) -> list[dict]:
    occurrences = Counter()
    fields = []
    for line in text.splitlines():
        label, separator, value = line.partition(":")
        name = FORM_FIELDS[form_type].get(label.strip())
        value = value.strip()
        if not separator or name is None or not value:
            continue
        occurrences[name] += 1
        fields.append({
            "field_name": name,
            "occurrence": occurrences[name],
            "extracted_value": value,
            "confidence": None,
            "bbox": None,
        })
    return fields
