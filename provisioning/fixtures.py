"""Deterministic synthetic fixtures and their separately stored answer keys."""

import hashlib
import zlib
from contextlib import closing
from io import BytesIO


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
    return _serialize_pdf(objects)


def _serialize_pdf(objects):
    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, contents in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode("ascii") + contents + b"\nendobj\n"
    startxref = len(pdf)
    size = len(objects) + 1
    pdf += f"xref\n0 {size}\n0000000000 65535 f \n".encode("ascii")
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    return pdf + f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode("ascii")


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


def scanned_fixtures():
    """Render the synthetic digital set into separate image-only PDFs."""
    fixtures = []
    for digital_pdf, digital_entry in digital_fixtures():
        pdf = _raster_pdf(digital_pdf)
        fields = {item["field_name"]: item["expected_value"] for item in digital_entry["fields"]}
        fixtures.append(fixture_entry(
            pdf,
            digital_entry["fixture_id"].removesuffix("-digital") + "-scanned",
            digital_entry["form_type"],
            digital_entry["person_key"],
            digital_entry["expected_forms"],
            fields,
            variant="scanned",
        ))
    return fixtures


def identifier_preview_fixtures():
    """Return two-page W-2 variants with identifiers in supplemental text."""
    from pypdf import PdfReader, PdfWriter

    person = "fixture-person-preview"
    items = [
        ("Employee SSN", "employee_ssn", synthetic_identifier("ssn", 8101)),
        ("Employer EIN", "employer_ein", synthetic_identifier("ein", 8101)),
        ("Wages", "wages", "36000.00"),
    ]
    pages = (
        form_lines("W-2", person, items),
        [
            "Synthetic attachment - archival references",
            f"Prior employee reference: {synthetic_identifier('ssn', 8102)}",
            f"Prior employer reference: {synthetic_identifier('ein', 8102)}",
        ],
    )
    writer = PdfWriter()
    for lines in pages:
        writer.append_pages_from_reader(PdfReader(BytesIO(digital_w2(lines)), strict=True))
    with BytesIO() as output:
        writer.write(output)
        digital = output.getvalue()

    fields = {name: value for _, name, value in items}
    return [
        fixture_entry(pdf, f"preview-w2-{variant}", "W-2", person, ["W-2"], fields, variant)
        for pdf, variant in ((digital, "digital"), (_raster_pdf(digital), "scanned"))
    ]


def _raster_pdf(digital_pdf):
    from pypdfium2 import PdfDocument

    with PdfDocument(digital_pdf) as source:
        page_refs = " ".join(f"{3 + 3 * index} 0 R" for index in range(len(source)))
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{page_refs}] /Count {len(source)} >>".encode("ascii"),
        ]
        for index in range(len(source)):
            with closing(source[index]) as page:
                objects.extend(_raster_page_objects(page, len(objects) + 2))
    return _serialize_pdf(objects)


def _raster_page_objects(page, image_ref):
    # Render synthetic Letter pages at 144 dpi, keeping only the raster.
    with closing(page.render(scale=2, grayscale=True)) as bitmap:
        width, height = bitmap.width, bitmap.height
        pixels = b"".join(
            bytes(bitmap.buffer[row:row + width])
            for row in range(0, height * bitmap.stride, bitmap.stride)
        )

    compressed = zlib.compress(pixels, level=9)
    image_header = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(compressed)} >>\nstream\n"
    )
    draw = b"q 612 0 0 792 0 0 cm /Scan Do Q"
    return (
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /XObject << /Scan {image_ref} 0 R >> >> "
            f"/Contents {image_ref + 1} 0 R >>"
        ).encode("ascii"),
        image_header.encode("ascii") + compressed + b"\nendstream",
        f"<< /Length {len(draw)} >>\nstream\n".encode("ascii") + draw + b"\nendstream",
    )
