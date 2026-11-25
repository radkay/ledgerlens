"""Bounding boxes for verified text-only synthetic pages."""

import re
from io import BytesIO
from itertools import groupby
from math import isfinite

from .fields import FORM_FIELDS, extract_fields

_IDENTIFIER = re.compile(r"(?<![0-9])(?:[0-9]{3}[- ][0-9]{2}[- ][0-9]{4}|"
                         r"[0-9]{2}[- ][0-9]{7}|[0-9]{9})(?![0-9])")
_LETTER = (0, 0, 612, 792)
_TEXT_PREFIX = [([], b"BT"), (["/F1", 14], b"Tf"), ([18], b"TL"), ([54, 738], b"Td")]
_PAGE_KEYS = {"/Type", "/Parent", "/MediaBox", "/CropBox", "/Resources", "/Contents", "/Rotate"}
_HELVETICA = {"/Type": "/Font", "/Subtype": "/Type1", "/BaseFont": "/Helvetica"}


def incomplete_coverage(page_count):
    return [
        {"page_index": index, "complete": False, "boxes": []}
        for index in range(page_count)
    ]


def digital_geometry(document, reader, texts, form_type):
    """Return page coverage and field boxes in extraction order."""
    import pdfplumber
    from pdfminer.pdfexceptions import PDFException
    from pypdf.errors import PyPdfError

    coverage = incomplete_coverage(len(texts))
    field_boxes = []
    try:
        with pdfplumber.open(BytesIO(document)) as layout:
            if set(reader.root_object) != {"/Type", "/Pages"}:
                return coverage, []
            for index, (pdf_page, layout_page, text) in enumerate(
                zip(reader.pages, layout.pages, texts, strict=True)
            ):
                expected_count = len(extract_fields(text, form_type)) if form_type else 0
                boxes = [None] * expected_count
                try:
                    positioned = _verified_lines(pdf_page, layout_page, text)
                    if positioned:
                        identifiers, positioned_fields = _page_boxes(index, positioned, form_type)
                        if len(positioned_fields) == expected_count:
                            boxes = positioned_fields
                            coverage[index] = {"page_index": index, "complete": True, "boxes": identifiers}
                except (PDFException, PyPdfError, KeyError, TypeError, ValueError, IndexError, AttributeError):
                    pass
                field_boxes.extend(boxes)
    except (PDFException, PyPdfError, KeyError, TypeError, ValueError, IndexError, AttributeError):
        return incomplete_coverage(len(texts)), []
    return coverage, field_boxes


def _verified_lines(page, layout, text):
    # Match the fixture renderer's full page paint sequence. An extra font,
    # image, annotation, transformation or unfamiliar operator is unverified.
    if (set(page) - _PAGE_KEYS or page.rotation or tuple(page.mediabox) != _LETTER
            or tuple(page.cropbox) != _LETTER):
        return []
    resources = page["/Resources"]
    if set(resources) != {"/Font"} or set(resources["/Font"]) != {"/F1"}:
        return []
    if resources["/Font"]["/F1"] != _HELVETICA:
        return []

    operations = page.get_contents().operations
    if operations[:4] != _TEXT_PREFIX or operations[-1:] != [([], b"ET")]:
        return []
    body = operations[4:-1]
    if len(body) % 2:
        return []
    for (args, operator), newline in zip(body[::2], body[1::2], strict=True):
        if (operator != b"Tj" or newline != ([], b"T*") or len(args) != 1
                or not isinstance(args[0], str) or not args[0].isascii() or not args[0].isprintable()):
            return []
    lines = [args[0] for args, _ in body[::2]]
    if lines != text.splitlines() or (layout.width, layout.height) != (612, 792):
        return []
    chars_by_line = [list(group) for _, group in groupby(layout.chars, key=lambda char: char["top"])]
    if ["".join(char["text"] for char in group) for group in chars_by_line] != lines:
        return []
    return list(zip(lines, chars_by_line, strict=True))


def _page_boxes(index, positioned, form_type):
    identifiers = []
    fields = []
    field_labels = FORM_FIELDS.get(form_type, {})
    for line, chars in positioned:
        for match in _IDENTIFIER.finditer(line):
            identifiers.append(_box(index, chars[match.start():match.end()]))
        label, colon, value = line.partition(":")
        if colon and label.strip() in field_labels and value.strip():
            start = len(label) + 1 + len(value) - len(value.lstrip())
            end = len(label) + 1 + len(value.rstrip())
            fields.append(_box(index, chars[start:end]))
    return identifiers, fields


def _box(index, chars):
    left = min(char["x0"] for char in chars) / 612
    top = min(char["top"] for char in chars) / 792
    right = max(char["x1"] for char in chars) / 612
    bottom = max(char["bottom"] for char in chars) / 792
    if (not all(isfinite(value) for value in (left, top, right, bottom))
            or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1)):
        raise ValueError("Invalid position on synthetic PDF page")
    return {"page_index": index, "left": left, "top": top, "right": right, "bottom": bottom}
