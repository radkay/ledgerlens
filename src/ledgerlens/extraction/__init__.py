"""Extract synthetic documents into Bronze attempt records."""

from .digital import extract_digital_pdf
from .record import field_id

__all__ = ["extract_digital_pdf", "field_id"]
