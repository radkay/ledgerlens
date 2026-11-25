"""Preview redaction tests using synthetic PDFs rendered by PDFium."""

from io import BytesIO
from pathlib import Path
import sys
import unittest

from pypdf import PdfReader
from pypdfium2 import PdfDocument
from reportlab.pdfgen.canvas import Canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledgerlens.processing.preview import IdentifierBox, masked_preview


def sample_pdf():
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(200, 100), invariant=1)
    canvas.setTitle("Original synthetic document")
    for serial in (1, 2):
        canvas.setFillColorRGB(1, 0, 0)
        canvas.rect(30, 55, 100, 20, stroke=0, fill=1)
        canvas.setFillColorRGB(1, 1, 1)
        canvas.drawString(35, 61, f"000-12-{serial:04d}")
        canvas.setFillColorRGB(0, 0, 1)
        canvas.rect(30, 15, 100, 20, stroke=0, fill=1)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


class MaskedPreviewTests(unittest.TestCase):
    def test_rebuilds_raster_pages_and_removes_identifier_pixels_and_text(self):
        boxes = [IdentifierBox(page, 0.15, 0.25, 0.65, 0.45) for page in range(2)]
        original = sample_pdf()
        preview = masked_preview(original, boxes)
        self.assertIsNotNone(preview)
        self.assertEqual(masked_preview(original, boxes), preview)

        reader = PdfReader(BytesIO(preview))
        self.assertEqual(len(reader.pages), 2)
        self.assertNotEqual(reader.metadata.title, "Original synthetic document")
        document = PdfDocument(preview)
        try:
            for index, page in enumerate(document):
                try:
                    bitmap = page.render(scale=1)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                        self.assertEqual(set(image.crop((30, 25, 130, 45)).get_flattened_data()), {(0, 0, 0)})
                        self.assertEqual(image.getpixel((60, 70)), (0, 0, 255))
                        self.assertEqual(reader.pages[index].extract_text(), "")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            document.close()

    def test_returns_no_artifact_if_any_box_missing_or_invalid(self):
        valid = IdentifierBox(0, 0.15, 0.25, 0.65, 0.45)
        for boxes in (
            [], [valid, None], [valid, IdentifierBox(2, 0.15, 0.25, 0.65, 0.45)],
            [IdentifierBox(0, -0.15, 0.25, 0.65, 0.45)],
            [IdentifierBox(0, 0.15, 0.25, float("nan"), 0.45)],
            [IdentifierBox(1, 0.15, 0.45, 0.65, 0.45)],
        ):
            with self.subTest(boxes=boxes):
                self.assertIsNone(masked_preview(sample_pdf(), boxes))
        self.assertIsNone(masked_preview(b"invalid PDF", [valid]))
