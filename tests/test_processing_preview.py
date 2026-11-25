"""Processing tests for fail-closed reviewer volume artifacts."""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdfium2 import PdfDocument

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledgerlens.processing.job import publish
from ledgerlens.processing.preview import IdentifierBox, PreviewVolume, masked_pages
from provisioning.fixtures import bootstrap_fixture, identifier_preview_fixtures
from tests.test_processing import START, MemoryInbox, MemoryStore


def record(page_index, boxes=(), *, complete=True):
    return {"page_index": page_index, "complete": complete, "boxes": [asdict(box) for box in boxes]}


def fixture_boxes(pdf):
    with pdfplumber.open(BytesIO(pdf)) as document:
        return [
            IdentifierBox(
                index, word["x0"] / page.width, word["top"] / page.height,
                word["x1"] / page.width, word["bottom"] / page.height,
            )
            for index, page in enumerate(document.pages)
            for word in page.extract_words()
            if re.fullmatch(r"000-[0-9]{2}-[0-9]{4}|00-[0-9]{7}", word["text"])
        ]


class MaskedPreviewTests(unittest.TestCase):
    def test_redacts_supplemental_identifiers_and_strips_attachments_and_metadata(self):
        (digital, _), (scanned, _) = identifier_preview_fixtures()
        boxes = fixture_boxes(digital)
        self.assertEqual([box.page_index for box in boxes], [0, 0, 1, 1])
        coverage = [record(0, boxes[:2]), record(1, boxes[2:])]

        writer = PdfWriter(clone_from=BytesIO(digital))
        writer.add_metadata({"/Title": "000-12-8102"})
        writer.add_attachment("original.txt", b"000-12-8102")
        embedded = BytesIO()
        writer.write(embedded)
        for variant, original in (("digital", embedded.getvalue()), ("scanned", scanned)):
            with self.subTest(variant=variant):
                partial = [coverage[0], record(1, boxes[2:3], complete=False)]
                self.assertEqual(set(dict(masked_pages(original, partial))), {0})
                pages = dict(masked_pages(original, coverage))
                self.assertEqual(set(pages), {0, 1})
                self.assertEqual(dict(masked_pages(original, coverage)), pages)
                for index, artifact in pages.items():
                    reader = PdfReader(BytesIO(artifact))
                    self.assertEqual(len(reader.pages), 1)
                    self.assertEqual(len(reader.attachments), 0)
                    self.assertNotEqual(reader.metadata.title, "000-12-8102")
                    self.assertEqual(reader.pages[0].extract_text(), "")

                    with PdfDocument(artifact) as document, closing(document[0]) as page:
                        with closing(page.render(scale=1)) as bitmap:
                            image = bitmap.to_pil().convert("RGB")
                            for box in (box for box in boxes if box.page_index == index):
                                region = image.crop((
                                    round(box.left * image.width), round(box.top * image.height),
                                    round(box.right * image.width), round(box.bottom * image.height),
                                ))
                                self.assertEqual(region.getextrema(), ((0, 0), (0, 0), (0, 0)))

    def test_withholds_malformed_duplicate_and_unlocated_coverage(self):
        (pdf, _), _ = identifier_preview_fixtures()
        box = IdentifierBox(0, 0.1, 0.1, 0.3, 0.2)
        good = record(0, [box])
        for coverage, expected in (
            (None, set()),
            ([], set()),
            ([good, record(1, [box])], {0}),
            ([good, record(1, [IdentifierBox(1, -0.1, 0.1, 0.3, 0.2)])], {0}),
            ([good, record(1, [IdentifierBox(1, 0.1, 0.1, float("nan"), 0.2)])], {0}),
            ([good, record(1, [IdentifierBox(1, 0.1, 0.1, 0.3, float("inf"))])], {0}),
            ([good, good, record(1)], {1}),
            ([good, {"complete": False}], set()),
            ([good, record(2)], set()),
        ):
            with self.subTest(coverage=coverage):
                self.assertEqual(set(dict(masked_pages(pdf, coverage))), expected)
        self.assertEqual(list(masked_pages(b"invalid PDF", [good])), [])
        with patch("ledgerlens.processing.preview.render_page",
                   side_effect=[ValueError("invalid render"), b"verified second page"]):
            self.assertEqual(dict(masked_pages(pdf, [good, record(1)])),
                             {1: b"verified second page"})

    def test_volume_writes_only_verified_pages_and_reuses_artifacts(self):
        (pdf, _), _ = identifier_preview_fixtures()
        boxes = fixture_boxes(pdf)[:2]
        doc_id = sha256(pdf).hexdigest()
        extraction_id = "synthetic-attempt"
        payload = {"identifier_coverage": [record(0, boxes), record(1, complete=False)]}
        attempt = {"status": "needs_review", "doc_id": doc_id,
                   "extraction_id": extraction_id, "raw_json": json.dumps(payload)}
        inbox = Mock()
        inbox.read.return_value = ("restricted", pdf)
        volume = PreviewVolume("ledgerlens_dev")
        self.assertEqual(volume.root, Path("/Volumes/ledgerlens_dev/review/masked_previews"))
        self.assertEqual(PreviewVolume("ledgerlens_demo").root,
                         Path("/Volumes/ledgerlens_demo/review/masked_previews"))

        with TemporaryDirectory() as directory:
            volume.root = Path(directory)
            volume.write_attempt(inbox, attempt | {"raw_json": "{}"})
            inbox.read.assert_not_called()
            volume.write_attempt(inbox, attempt)
            path = volume.root / doc_id / extraction_id / "0.pdf"
            stamp = path.stat().st_mtime_ns
            with patch("ledgerlens.processing.preview.render_page") as render:
                volume.write_attempt(inbox, attempt)
                render.assert_not_called()
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            self.assertEqual(list(volume.root.rglob("*.*")), [path])

    def test_processing_job_passes_current_attempt_to_preview_volume(self):
        from ledgerlens.extraction import extract_digital_pdf
        from pypdf import __version__

        pdf, fixture = bootstrap_fixture()
        inbox, store = MemoryInbox(), MemoryStore()
        inbox.pdfs = {fixture["sha256"]: pdf}

        def extract(*args):
            attempt = extract_digital_pdf(*args)
            payload = json.loads(attempt["raw_json"])
            payload["identifier_coverage"] = [record(0, fixture_boxes(pdf))]
            return attempt | {"raw_json": json.dumps(payload)}

        with TemporaryDirectory() as directory:
            volume = PreviewVolume("ledgerlens_dev")
            volume.root = Path(directory)
            publish(store, inbox, extract, extractor_version=__version__, job_run_id="preview",
                    trigger_type="manual", clock=lambda: START, preview_volume=volume)
            artifact, = volume.root.glob("*/*/0.pdf")
            self.assertEqual(artifact.parent.parent.name, fixture["sha256"])
            self.assertEqual(PdfReader(artifact).pages[0].extract_text(), "")
