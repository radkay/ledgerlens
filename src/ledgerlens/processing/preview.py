"""Build a raster-only reviewer PDF from verified extraction boxes."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor

from PIL import Image
from pypdfium2 import PdfDocument, PdfiumError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

MAX_PAGES = 10
MAX_PAGE_PIXELS = 4_000_000
SCALE = 1.5
PADDING = 2


@dataclass(frozen=True)
class IdentifierBox:
    """Verified zero-based page and normalized top-left display coordinates."""

    page_index: int
    left: float
    top: float
    right: float
    bottom: float

    def valid(self, page_count):
        coordinates = (self.left, self.top, self.right, self.bottom)
        return (
            type(self.page_index) is int and 0 <= self.page_index < page_count
            and all(type(value) in (float, int) for value in coordinates)
            and 0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1
        )

    def pixels(self, width, height):
        return (
            max(0, floor(self.left * width) - PADDING),
            max(0, floor(self.top * height) - PADDING),
            min(width, ceil(self.right * width) + PADDING),
            min(height, ceil(self.bottom * height) + PADDING),
        )


def masked_preview(pdf, verified_identifier_boxes):
    """Return fresh PDF bytes, or None when any identifier box is unavailable.

    The caller must supply one box for every identifier occurrence, including
    None for each unlocated occurrence. An empty input cannot establish coverage.
    """
    boxes = tuple(verified_identifier_boxes)
    if not boxes or any(not isinstance(box, IdentifierBox) for box in boxes):
        return None
    try:
        document = PdfDocument(pdf)
    except PdfiumError:
        return None
    try:
        if not 1 <= len(document) <= MAX_PAGES or any(not box.valid(len(document)) for box in boxes):
            return None
        output = BytesIO()
        canvas = Canvas(output, invariant=1, pageCompression=1)
        for page_index, page in enumerate(document):
            try:
                width, height = page.get_size()
                if ceil(width * SCALE) * ceil(height * SCALE) > MAX_PAGE_PIXELS:
                    return None
                bitmap = page.render(scale=SCALE, draw_annots=False, may_draw_forms=False)
                try:
                    # A fresh image only retains the rendered pixels.
                    rendered = bitmap.to_pil().convert("RGB")
                    image = Image.frombytes("RGB", rendered.size, rendered.tobytes())
                finally:
                    bitmap.close()
                for box in boxes:
                    if box.page_index == page_index:
                        image.paste((0, 0, 0), box.pixels(*image.size))
                canvas.setPageSize((width, height))
                canvas.drawImage(ImageReader(image), 0, 0, width=width, height=height)
                canvas.showPage()
            finally:
                page.close()
        canvas.save()
        return output.getvalue()
    except PdfiumError:
        return None
    finally:
        document.close()
