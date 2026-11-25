"""Build a raster-only reviewer PDF from verified extraction boxes."""

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import ceil, floor
from pathlib import Path
import re

from PIL import Image
from pypdfium2 import PdfDocument, PdfiumError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .storage import CATALOGS

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


def verified_pages(coverage, page_count):
    """Resolve complete coverage, withholding malformed or duplicate pages."""
    verified = {}
    seen = set()
    for record in coverage:
        index = record.get("page_index") if isinstance(record, dict) else None
        if type(index) is not int or not 0 <= index < page_count:
            return {}  # An unlocated coverage record cannot be isolated to one page.
        if index in seen:
            verified.pop(index, None)
            continue
        seen.add(index)
        boxes = record.get("boxes")
        if record.get("complete") is not True or not isinstance(boxes, list):
            continue
        try:
            parsed = [IdentifierBox(**box) for box in boxes]
        except TypeError:
            continue
        if all(box.page_index == index and box.valid(page_count) for box in parsed):
            verified[index] = parsed
    return verified


def render_page(page, boxes):
    width, height = page.get_size()
    if width <= 0 or height <= 0 or ceil(width * SCALE) * ceil(height * SCALE) > MAX_PAGE_PIXELS:
        return None
    with closing(page.render(scale=SCALE, draw_annots=False, may_draw_forms=False)) as bitmap:
        rendered = bitmap.to_pil().convert("RGB")
        image = Image.frombytes("RGB", rendered.size, rendered.tobytes())
    for box in boxes:
        image.paste((0, 0, 0), box.pixels(*image.size))
    output = BytesIO()
    canvas = Canvas(output, pagesize=(width, height), invariant=1, pageCompression=1)
    canvas.drawImage(ImageReader(image), 0, 0, width=width, height=height)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def masked_pages(pdf, coverage, *, existing=None):
    """Yield fresh single-page raster PDFs for verified pages."""
    if not isinstance(coverage, list) or not coverage:
        return
    try:
        document = PdfDocument(pdf)
    except PdfiumError:
        return
    with closing(document):
        if not 1 <= len(document) <= MAX_PAGES:
            return
        for index, boxes in sorted(verified_pages(coverage, len(document)).items()):
            if existing is not None and existing(index):
                continue
            try:
                with closing(document[index]) as page:
                    artifact = render_page(page, boxes)
            except (PdfiumError, OSError, ValueError, OverflowError):
                continue
            if artifact is not None:
                yield index, artifact


class PreviewVolume:
    """Publish only fully rendered pages under immutable extraction paths."""

    def __init__(self, catalog):
        if catalog not in CATALOGS:
            raise ValueError("Unexpected catalog")
        self.root = Path(f"/Volumes/{catalog}/review/masked_previews")

    def write_attempt(self, inbox, attempt):
        if attempt is None or attempt["status"] == "failed":
            return
        try:
            payload = json.loads(attempt["raw_json"])
        except (TypeError, ValueError):
            return
        coverage = payload.get("identifier_coverage") if isinstance(payload, dict) else None
        if not isinstance(coverage, list) or not any(
            isinstance(record, dict) and record.get("complete") is True for record in coverage
        ):
            return

        doc_id, extraction_id = attempt["doc_id"], attempt["extraction_id"]
        if not re.fullmatch(r"[a-f0-9]{64}", doc_id) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", extraction_id,
        ):
            raise ValueError("Invalid preview identifier")

        folder = self.root / doc_id / extraction_id

        def page_path(index):
            return folder / f"{index}.pdf"

        _, pdf = inbox.read(doc_id)
        if sha256(pdf).hexdigest() != doc_id:
            raise ValueError("Inbox content differs from frozen hash")
        for index, artifact in masked_pages(pdf, coverage, existing=lambda i: page_path(i).is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            path = page_path(index)
            staging = path.with_suffix(".tmp")
            try:
                staging.write_bytes(artifact)
                staging.replace(path)
            finally:
                staging.unlink(missing_ok=True)
