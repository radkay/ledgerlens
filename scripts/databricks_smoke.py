"""Exercise the provisioned jobs and verify serving and denial paths."""

import base64
import json
import os
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


TASK_ERROR = re.compile(
    r"(?:Processing|Evaluation) job failed \([A-Za-z_][A-Za-z0-9_.]{0,79}\)"
    r"|ModuleNotFoundError: No module named '[A-Za-z_][A-Za-z0-9_.]{0,79}'"
    r"|Expected Python 3\.12\.3, found [0-9.]{3,20}"
    r"|(?:AnalysisException|SparkRuntimeException|SparkConnectGrpcException|PySparkRuntimeError)"
    r":\s*\[[A-Z][A-Z0-9_.]{2,79}\]"
)


def request(url, *, headers=None, body=None):
    with urlopen(Request(url, data=body, headers=headers or {}), timeout=45) as result:
        return json.load(result)


def task_error(api, run):
    failed_task = next(
        (task for task in run.get("tasks", []) if task.get("state", {}).get("result_state") != "SUCCESS"),
        {},
    )
    task_run_id = failed_task.get("run_id")
    if type(task_run_id) is not int or task_run_id <= 0:
        return ""
    details = [f"task run {task_run_id}"]
    status_code = failed_task.get("status", {}).get("termination_details", {}).get("code", "")
    if isinstance(status_code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", status_code):
        details.append(status_code)
    output = {}
    try:
        output = api(f"/api/2.2/jobs/runs/get-output?run_id={task_run_id}")
    except HTTPError as error:
        details.append(f"task output HTTP {error.code}")
    except (OSError, ValueError):
        details.append("task output unavailable")
    # Connector errors may contain data. Log only bounded exception classes and error codes.
    messages = (
        output.get("error"), output.get("error_trace"), output.get("logs"),
        failed_task.get("state", {}).get("state_message"),
    )
    for message in messages:
        if isinstance(message, str) and (match := TASK_ERROR.search(message)):
            details.append(match.group())
            break
    return ", ".join(details)


def verify_permission_denial(table, status):
    state = status["state"]
    error = status.get("error", {})
    code = error.get("error_code")
    sql_state = error.get("sql_state")
    # Report structured fields only. SQL error messages can include sensitive data.
    details = [f"state={state}"]
    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code):
        details.append(f"error_code={code}")
    if isinstance(sql_state, str) and re.fullmatch(r"[A-Z0-9]{5}", sql_state):
        details.append(f"sql_state={sql_state}")
    summary = ", ".join(details)
    if state == "FAILED" and (
        sql_state == "42501" or code in ("PERMISSION_DENIED", "INSUFFICIENT_PERMISSIONS")
    ):
        print(f"Permission denial confirmed for {table} ({summary})", flush=True)
        return
    raise RuntimeError(f"Expected an explicit permission denial for {table} ({summary})")


def main():
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    parsed = urlsplit(host)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.username:
        raise ValueError("Use an HTTPS Databricks workspace origin")
    catalog = os.environ["LEDGERLENS_CATALOG"]
    if catalog not in {"ledgerlens_dev", "ledgerlens_demo"}:
        raise ValueError("Unexpected catalog")
    client = os.environ["DATABRICKS_CLIENT_ID"]
    secret = os.environ["DATABRICKS_CLIENT_SECRET"]
    warehouse = os.environ["DATABRICKS_WAREHOUSE_ID"]
    processing_job_id = int(os.environ["DATABRICKS_JOB_ID"])
    evaluation_job_id = int(os.environ["DATABRICKS_EVALUATION_JOB_ID"])
    if not all((client, secret, warehouse)) or min(processing_job_id, evaluation_job_id) <= 0:
        raise ValueError("Integration configuration is incomplete")
    auth = base64.b64encode(f"{client}:{secret}".encode()).decode()
    token = request(
        host + "/oidc/v1/token",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode(),
    )["access_token"]

    def api(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        return request(host + path, headers=headers, body=body)

    def wait_for(fetch, finished):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            result = fetch()
            if finished(result):
                return result
            time.sleep(5)
        raise TimeoutError("Databricks operation did not finish")

    def run_job(job_id, role):
        run_id = api("/api/2.1/jobs/run-now", {"job_id": job_id})["run_id"]
        print(f"{role} job started (Databricks run {run_id})", flush=True)
        run = wait_for(
            lambda: api(f"/api/2.2/jobs/runs/get?run_id={run_id}"),
            lambda item: item["state"]["life_cycle_state"] in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"},
        )
        state = run["state"].get("result_state") or run["state"]["life_cycle_state"]
        if state != "SUCCESS":
            error = task_error(api, run)
            detail = f": {error}" if error else ""
            raise RuntimeError(f"{role} job did not succeed (Databricks run {run_id}, {state}){detail}")
        print(f"{role} job succeeded (Databricks run {run_id})", flush=True)
        return run_id

    def sql(statement, **parameters):
        result = api("/api/2.0/sql/statements", {
            "warehouse_id": warehouse,
            "statement": statement,
            "parameters": [
                {"name": name, "type": "STRING", "value": str(value)} for name, value in parameters.items()
            ],
            "wait_timeout": "10s",
        })
        if result["status"]["state"] in {"PENDING", "RUNNING"}:
            path = "/api/2.0/sql/statements/" + result["statement_id"]
            result = wait_for(
                lambda: api(path),
                lambda item: item["status"]["state"] not in {"PENDING", "RUNNING"},
            )
        return result

    job_run_id = run_job(processing_job_id, "Processing")
    publication = sql(f"""
        SELECT latest.run_id
        FROM {catalog}.gold.latest_publication AS latest
        WHERE EXISTS (
            SELECT 1 FROM {catalog}.ops.pipeline_runs AS event
            WHERE event.run_id = latest.run_id
              AND event.job_run_id = :job_run_id
              AND event.trigger_type = 'manual'
              AND event.status IN ('published', 'skipped_duplicate')
        )
    """, job_run_id=job_run_id)
    rows = publication.get("result", {}).get("data_array", [])
    if publication["status"]["state"] != "SUCCEEDED" or len(rows) != 1:
        raise RuntimeError("The processing invocation did not produce or reuse the current publication")
    published_run_id = rows[0][0]
    lineage = sql(
        f"SELECT 1 FROM {catalog}.gold.current_lineage WHERE run_id = :run_id LIMIT 1",
        run_id=published_run_id,
    )
    if lineage["status"]["state"] != "SUCCEEDED" or not lineage.get("result", {}).get("data_array"):
        raise RuntimeError("Expected published Gold lineage for the processing invocation")
    run_job(evaluation_job_id, "Evaluation")
    accuracy = sql(f"""
        SELECT 1 FROM {catalog}.gold.current_extraction_accuracy
        WHERE run_id = :run_id AND form_type = 'W-2' AND variant = 'digital'
          AND expected_count > 0 AND exact_match_count BETWEEN 0 AND expected_count
        LIMIT 1
    """, run_id=published_run_id)
    if accuracy["status"]["state"] != "SUCCEEDED" or not accuracy.get("result", {}).get("data_array"):
        raise RuntimeError("Expected valid digital W-2 accuracy for the processing invocation")
    for table in ("silver.fields", "eval.answer_key"):
        result = sql(f"SELECT 1 FROM {catalog}.{table} LIMIT 1")
        verify_permission_denial(table, result["status"])
    print("Processing and evaluation jobs, Gold publication, lineage and accuracy, and Silver/eval denials passed")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Databricks HTTP request failed with status {error.code}") from None
