"""Bounded reads over the contracted Gold serving views."""

import re
from typing import Protocol

CATALOGS = frozenset({"ledgerlens_dev", "ledgerlens_demo"})
OUTPUT_FIELDS = ("wages", "other_income", "mortgage_interest", "status")
ROW_LIMIT = 25
FETCH_LIMIT = ROW_LIMIT + 1


class SqlReader(Protocol):
    def __call__(
        self, statement: str, parameters: dict[str, str], *, row_limit: int,
    ) -> list[list[str | None]]: ...


class GoldTools:
    def __init__(self, catalog: str, read: SqlReader):
        if catalog not in CATALOGS:
            raise ValueError("Select a contracted LedgerLens catalog.")
        self._gold = f"{catalog}.gold"
        self._read = read

    def _query(self, statement, parameters=None, *, row_limit=FETCH_LIMIT):
        rows = self._read(statement, parameters or {}, row_limit=row_limit)
        if len(rows) > row_limit:
            raise RuntimeError("SQL response exceeded the requested row limit.")
        return rows

    def pin(self) -> "PublishedGold":
        rows = self._query(f"SELECT run_id FROM {self._gold}.latest_publication LIMIT 1", row_limit=1)
        if not rows or not rows[0][0]:
            raise LookupError("No published Gold run is available.")
        return PublishedGold(self, rows[0][0])


class PublishedGold:
    def __init__(self, tools: GoldTools, run_id: str):
        self._tools = tools
        self.run_id = run_id

    def trace_value(self, gold_row_id: str = "", output_field: str = "") -> dict:
        """Read Gold outputs and their source links, optionally filtered by row and field."""
        validate_trace(gold_row_id, output_field)
        gold = self._tools._gold
        rows = self._tools._query(f"""
            WITH published_outputs AS (
                SELECT gold_row_id, person_key, tax_year, CAST(NULL AS STRING) AS form_type,
                       output_field, CAST(output_value AS STRING) AS output_value
                FROM {gold}.published_person_year
                UNPIVOT EXCLUDE NULLS (output_value FOR output_field IN (wages, other_income, mortgage_interest))
                WHERE run_id = :run_id
                UNION ALL
                SELECT gold_row_id, person_key, tax_year, form_type, output_field, output_value
                FROM {gold}.published_prefill WHERE run_id = :run_id
                UNION ALL
                SELECT gold_row_id, person_key, tax_year, expected_form, 'status', status
                FROM {gold}.published_missing_documents WHERE run_id = :run_id
            )
            SELECT o.gold_row_id, o.person_key, o.tax_year, o.form_type, o.output_field,
                   o.output_value, l.doc_id, l.field_id, l.extraction_id, l.decision_id,
                   l.extractor_name, l.extractor_version, l.model_name, l.model_version
            FROM published_outputs AS o
            LEFT JOIN {gold}.published_lineage AS l
              ON l.run_id = :run_id AND l.gold_row_id = o.gold_row_id AND l.output_field = o.output_field
            WHERE (:gold_row_id = '' OR o.gold_row_id = :gold_row_id)
              AND (:output_field = '' OR o.output_field = :output_field)
            ORDER BY o.gold_row_id, o.output_field, l.doc_id, l.field_id
            LIMIT {FETCH_LIMIT}
        """, {"run_id": self.run_id, "gold_row_id": gold_row_id, "output_field": output_field})
        columns = (
            "gold_row_id", "person_key", "tax_year", "form_type", "output_field", "output_value",
            "doc_id", "field_id", "extraction_id", "decision_id", "extractor_name",
            "extractor_version", "model_name", "model_version",
        )
        return {
            "run_id": self.run_id,
            "rows": [dict(zip(columns, row, strict=True)) for row in rows[:ROW_LIMIT]],
            "truncated": len(rows) > ROW_LIMIT,
        }

    def documents_needing_review(self) -> dict:
        """Read the published count and the separate live list of documents needing review."""
        gold = self._tools._gold
        summary = self._tools._query(f"""
            SELECT document_count FROM {gold}.published_review_summary
            WHERE run_id = :run_id AND status = :status
            LIMIT 1
        """, {"run_id": self.run_id, "status": "needs_review"}, row_limit=1)
        live = self._tools._query(f"""
            SELECT doc_id, status, state_at FROM {gold}.current_document_state
            WHERE status = :status
            ORDER BY state_at DESC, doc_id
            LIMIT {FETCH_LIMIT}
        """, {"status": "needs_review"})
        return {
            "run_id": self.run_id,
            "published_document_count": int(summary[0][0]) if summary else 0,
            "live_documents": [
                dict(zip(("doc_id", "status", "state_at"), row, strict=True))
                for row in live[:ROW_LIMIT]
            ],
            "live_documents_truncated": len(live) > ROW_LIMIT,
        }


def validate_trace(gold_row_id: str, output_field: str) -> None:
    if not isinstance(gold_row_id, str) or (gold_row_id and not re.fullmatch(r"[a-f0-9]{64}", gold_row_id)):
        raise ValueError("Provide a 64-character lowercase hexadecimal Gold row ID or omit it.")
    if output_field not in ("", *OUTPUT_FIELDS):
        raise ValueError("Select wages, other_income, mortgage_interest, or status, or omit the field.")
