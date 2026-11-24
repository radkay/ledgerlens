"""Deterministic synthetic bootstrap fixture and its separately stored answer key."""

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
    doc_id = hashlib.sha256(pdf).hexdigest()
    entry = {
        "fixture_id": "bootstrap-w2-digital",
        "filename": f"{doc_id}.pdf",
        "sha256": doc_id,
        "form_type": "W-2",
        "variant": "digital",
        "tax_year": 2025,
        "person_key": "fixture-person-001",
        "expected_forms": ["W-2", "1099"],
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
