"""Databricks-backed Gold reads and delegated operator actions."""

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime

from .dashboard import Accuracy, PipelineEvent, Publication, Schedule
from .fixtures import ASSETS
from .outputs import Lineage, Prefill
from .workbench import Field, ReviewDocument, validate_decision

CATALOGS = {"dev": "ledgerlens_dev", "demo": "ledgerlens_demo"}
BOOTSTRAP_PREVIEWS = {
    # The masked copy of the repository's deterministic digital W-2 fixture.
    "65912382990c61f773d6b2a109533c7e43d657c8dc52c597e40973b071d23dd8": ASSETS / "bootstrap-w2-masked.pdf",
}
ROWS_LIMIT = 10_000


def utc_timestamp(value):
    timestamp = datetime.fromisoformat(value)
    return timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)


class Sql:
    def __init__(self, workspace, warehouse, token):
        self.workspace = workspace
        self.warehouse = warehouse
        self.token = token

    def execute(self, statement, **values):
        path = "/api/2.0/sql/statements"
        self.workspace.trace.append(("SQL statement", " ".join(statement.split())))
        response = self.workspace.call("POST", path, self.token(), json={
            "warehouse_id": self.warehouse,
            "statement": statement,
            "parameters": [
                {"name": name, "value": value, "type": "STRING"} for name, value in values.items()
            ],
            "wait_timeout": "10s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "row_limit": ROWS_LIMIT,
        })
        if response.get("status", {}).get("state") != "SUCCEEDED":
            raise OSError("Databricks SQL did not confirm the statement.")
        if response.get("manifest", {}).get("truncated"):
            raise OSError("The SQL result exceeded the app row limit.")
        result = response.get("result", {})
        rows = result.get("data_array", [])
        while (next_chunk := result.get("next_chunk_index")) is not None:
            statement_id = response["statement_id"]
            result = self.workspace.call("GET", f"{path}/{statement_id}/result/chunks/{next_chunk}", self.token())
            rows.extend(result.get("data_array", []))
        return rows


class GoldReader:
    def __init__(self, sql, environment):
        self.sql = sql
        self.gold = f"{CATALOGS[environment]}.gold"

    def latest_publication(self):
        rows = self.sql.execute(f"SELECT run_id, publication_seq, published_at FROM {self.gold}.latest_publication")
        return Publication(rows[0][0], int(rows[0][1]), utc_timestamp(rows[0][2])) if rows else None

    def document_states(self):
        rows = self.sql.execute(f"SELECT status, count(*) FROM {self.gold}.current_document_state GROUP BY status")
        return {status: int(count) for status, count in rows}

    def review_summary(self, run_id):
        rows = self.sql.execute(f"""SELECT status, document_count FROM {self.gold}.current_review_summary
                                    WHERE run_id = :run_id ORDER BY status""", run_id=run_id)
        return {status: int(count) for status, count in rows}

    def confidence_scores(self, run_id):
        # The contract does not expose confidence aggregates to the Gold reader.
        return None

    def extraction_accuracy(self, run_id):
        rows = self.sql.execute(f"""SELECT form_type, variant, expected_count, exact_match_count
                                    FROM {self.gold}.current_extraction_accuracy WHERE run_id = :run_id
                                    ORDER BY form_type, variant""", run_id=run_id)
        return [Accuracy(form, variant, int(count), int(matches)) for form, variant, count, matches in rows]

    def lineage_keys(self, run_id, key):
        if key not in {"doc_id", "gold_row_id"}:
            raise ValueError("Unknown lineage direction.")
        rows = self.sql.execute(f"""SELECT DISTINCT {key} FROM {self.gold}.current_lineage
                                    WHERE run_id = :run_id ORDER BY {key}""", run_id=run_id)
        return [row[0] for row in rows]

    def lineage(self, run_id, *, doc_id=None, gold_row_id=None):
        if (doc_id is None) == (gold_row_id is None):
            raise ValueError("Select exactly one lineage direction.")
        key, value = ("doc_id", doc_id) if doc_id is not None else ("gold_row_id", gold_row_id)
        rows = self.sql.execute(f"""
            SELECT gold_row_id, output_field, doc_id, field_id, extraction_id, decision_id,
                   extractor_name, extractor_version, model_name, model_version
            FROM {self.gold}.current_lineage WHERE run_id = :run_id AND {key} = :source
            ORDER BY gold_row_id, output_field, doc_id, field_id
        """, run_id=run_id, source=value)
        return [Lineage(*row) for row in rows]

    def prefill(self, run_id):
        rows = self.sql.execute(f"""
            SELECT gold_row_id, person_key, tax_year, form_type, output_field, output_value
            FROM {self.gold}.current_prefill WHERE run_id = :run_id
            ORDER BY person_key, tax_year, form_type, output_field
        """, run_id=run_id)
        return [Prefill(row_id, person, int(year), form, field, value)
                for row_id, person, year, form, field, value in rows]


class LiveWorkbench:
    def __init__(self, sql, environment, actor_id):
        self.sql = sql
        self.review = f"{CATALOGS[environment]}.review"
        self.actor_id = actor_id

    def reasons(self):
        result = defaultdict(list)
        rows = self.sql.execute(f"""SELECT action, reason_code FROM {self.review}.reason_codes
                                    WHERE enabled ORDER BY action, reason_code""")
        for action, reason in rows:
            result[action].append(reason)
        return result

    def documents(self, doc_id=None):
        where = "WHERE doc_id = :doc_id" if doc_id is not None else ""
        rows = self.sql.execute(f"""
            SELECT doc_id, extraction_id, state_at, status, field_id, field_name,
                   extracted_value, final_value, confidence
            FROM {self.review}.current_workbench {where}
            ORDER BY doc_id, field_id
        """, **({"doc_id": doc_id} if doc_id is not None else {}))
        documents = {}
        for key, extraction_id, state_at, status, field_id, name, extracted, final, confidence in rows:
            if key not in documents:
                documents[key] = (extraction_id, utc_timestamp(state_at), status, [])
            if field_id is not None:
                score = float(confidence) if confidence is not None else None
                documents[key][3].append(Field(field_id, name, extracted, final, score).masked())
        return tuple(ReviewDocument(key, extraction, state_at, status, tuple(fields))
                     for key, (extraction, state_at, status, fields) in documents.items())

    def preview(self, doc_id):
        path = BOOTSTRAP_PREVIEWS.get(doc_id)
        return path.read_bytes() if path else None

    def append_decision(self, decision, state_at):
        documents = self.documents(decision.doc_id)
        if not documents or documents[0].state_at != state_at:
            raise ValueError("The document state changed. Reload the document.")
        validate_decision(decision, documents[0], self.reasons(), self.actor_id)
        values = asdict(decision)
        values["decided_at"] = decision.decided_at.isoformat()
        result = self.sql.execute(f"""
            INSERT INTO {self.review}.decisions
                (decision_id, doc_id, extraction_id, field_id, action, reason_code, corrected_value, actor_id, decided_at)
            SELECT :decision_id, :doc_id, :extraction_id, :field_id, :action, :reason_code,
                   :corrected_value, :actor_id, CAST(:decided_at AS TIMESTAMP)
            WHERE EXISTS (
                SELECT 1 FROM {self.review}.current_workbench
                WHERE doc_id = :doc_id AND extraction_id <=> :extraction_id
                  AND state_at = CAST(:state_at AS TIMESTAMP) AND status = :status
                  AND (:field_id IS NULL OR field_id = :field_id)
            ) AND EXISTS (
                SELECT 1 FROM {self.review}.reason_codes
                WHERE action = :action AND reason_code = :reason_code AND enabled
            )
        """, state_at=state_at.isoformat(), status=documents[0].status, **values)
        if result and result[0][0] == "0":
            raise ValueError("The document state or review reason changed. Reload the document.")
        if not result or result[0][0] != "1":
            raise OSError("Databricks did not confirm the append.")


class LiveBatch:
    def __init__(self, sql, environment, job_id):
        self.sql = sql
        self.ops = f"{CATALOGS[environment]}.ops"
        self.job_id = job_id
        self.job_name = f"ledgerlens-{environment}-process"

    def jobs(self, method, endpoint, **kwargs):
        return self.sql.workspace.call(method, f"/api/2.2/jobs/{endpoint}", self.sql.token(), **kwargs)

    def job_settings(self):
        job = self.jobs("GET", "get", params={"job_id": self.job_id})
        settings = job.get("settings", {})
        if settings.get("name") != self.job_name or settings.get("max_concurrent_runs") != 1:
            raise ValueError("Configured processing job does not match the environment contract.")
        return settings

    def schedule(self):
        schedule = self.job_settings().get("schedule")
        return Schedule(schedule["quartz_cron_expression"], schedule["timezone_id"],
                        schedule["pause_status"] == "PAUSED") if schedule else None

    def active_job_run(self):
        runs = self.jobs("GET", "runs/list", params={"job_id": self.job_id, "active_only": "true", "limit": 1})
        return str(runs["runs"][0]["run_id"]) if runs.get("runs") else None

    def run_batch(self, request_id):
        self.job_settings()
        response = self.jobs("POST", "run-now", json={
            "job_id": self.job_id, "idempotency_token": request_id, "queue": {"enabled": False},
        })
        return str(response["run_id"])

    def terminal_job_state(self, job_run_id):
        response = self.jobs("GET", "runs/get", params={"run_id": job_run_id})
        state = response.get("state", {})
        if state.get("life_cycle_state") in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            return state.get("result_state") or state["life_cycle_state"]
        return None

    def pipeline_runs(self, job_run_id):
        rows = self.sql.execute(f"""
            SELECT run_id, event_seq, event_at, status, job_run_id, trigger_type, detail_code
            FROM {self.ops}.pipeline_runs WHERE job_run_id = :job_run_id
            QUALIFY row_number() OVER (ORDER BY event_at DESC, event_seq DESC, run_id DESC) = 1
        """, job_run_id=job_run_id)
        return [PipelineEvent(run_id, int(seq), utc_timestamp(at), status, job_id, trigger, detail)
                for run_id, seq, at, status, job_id, trigger, detail in rows]
