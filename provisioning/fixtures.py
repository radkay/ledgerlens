"""Deterministic synthetic fixtures and their separately stored answer keys."""

import hashlib


def synthetic_identifier(kind, serial):
    if not 1 <= serial <= 9999:
        raise ValueError("Fixture serial must be between 1 and 9999")
    if kind == "ssn":
        return f"000-12-{serial:04d}"
    if kind == "ein":
        return f"00-120{serial:04d}"
    raise ValueError("Unknown fixture identifier kind")


def digital_w2(lines):
    """Build a one-page ASCII PDF with an extractable text layer."""
    stream = b"BT /F1 14 Tf 18 TL 54 738 Td\n"
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream += f"({escaped}) Tj T*\n".encode("ascii")
    stream += b"ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
    )
    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, contents in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode("ascii") + contents + b"\nendobj\n"
    startxref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    return pdf + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode("ascii")


def bootstrap_fixture():
    fields = {
        "employee_ssn": synthetic_identifier("ssn", 1),
        "employer_ein": synthetic_identifier("ein", 1),
        "wages": "25000.00",
    }
    pdf = digital_w2([
        "Synthetic W-2 - 2025 - LedgerLens Sample",
        "Fixture person key: fixture-person-001",
        f"Employee SSN: {fields['employee_ssn']}",
        f"Employer EIN: {fields['employer_ein']}",
        f"Wages: {fields['wages']}",
    ])
    return fixture_entry(pdf, "bootstrap-w2-digital", "W-2", "fixture-person-001",
                         ["W-2", "1099"], fields)


def digital_fixtures():
    """Return the digital fixture set while preserving the bootstrap entry point."""
    fixtures = [bootstrap_fixture()]
    for form, person, expected_forms, items in supplemental_forms():
        pdf = digital_w2(form_lines(form, person, items))
        fields = {name: value for _, name, value in items}
        fixture_id = f"bootstrap-{form.lower()}-digital"
        fixtures.append(fixture_entry(pdf, fixture_id, form, person, expected_forms, fields))
    return fixtures


def supplemental_forms():
    return (
        ("1099", "fixture-person-001", ["W-2", "1099"], [
            ("Recipient SSN", "recipient_ssn", synthetic_identifier("ssn", 1)),
            ("Payer TIN", "payer_tin", synthetic_identifier("ein", 2)),
            ("Other income", "other_income", "1150.00"),
        ]),
        ("1098", "fixture-person-002", ["1098"], [
            ("Borrower SSN", "borrower_ssn", synthetic_identifier("ssn", 2)),
            ("Lender TIN", "lender_tin", synthetic_identifier("ein", 3)),
            ("Mortgage interest", "mortgage_interest", "4800.50"),
        ]),
    )


def form_lines(form, person, items):
    return [
        f"Synthetic {form} - 2025 - LedgerLens Sample",
        f"Fixture person key: {person}",
        *(f"{label}: {value}" for label, _, value in items),
    ]


def fixture_entry(pdf, fixture_id, form, person, expected_forms, fields, variant="digital"):
    doc_id = hashlib.sha256(pdf).hexdigest()
    entry = {
        "fixture_id": fixture_id,
        "filename": f"{doc_id}.pdf",
        "sha256": doc_id,
        "form_type": form,
        "variant": variant,
        "tax_year": 2025,
        "person_key": person,
        "expected_forms": expected_forms,
        "fields": [
            {
                "field_id": hashlib.sha256(f"{doc_id}:{name}:1".encode()).hexdigest(),
                "field_name": name,
                "expected_value": value,
            }
            for name, value in fields.items()
        ],
    }
    return pdf, entry
