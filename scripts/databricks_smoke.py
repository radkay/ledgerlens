"""Exercise the provisioned processing job and verify serving/denial paths."""

import base64
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def request(url, *, headers=None, body=None):
    with urlopen(Request(url, data=body, headers=headers or {}), timeout=45) as result:
        return json.load(result)


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
    job_id = int(os.environ["DATABRICKS_JOB_ID"])
    if not all((client, secret, warehouse)) or job_id <= 0:
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

    run_id = api("/api/2.1/jobs/run-now", {"job_id": job_id})["run_id"]
    run = wait_for(
        lambda: api(f"/api/2.1/jobs/runs/get?run_id={run_id}"),
        lambda item: item["state"]["life_cycle_state"] in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"},
    )
    if run["state"].get("result_state") != "SUCCESS":
        raise RuntimeError("Processing job did not succeed")

    def sql(statement):
        result = api("/api/2.0/sql/statements", {
            "warehouse_id": warehouse,
            "statement": statement,
            "wait_timeout": "10s",
        })
        if result["status"]["state"] in {"PENDING", "RUNNING"}:
            path = "/api/2.0/sql/statements/" + result["statement_id"]
            result = wait_for(
                lambda: api(path),
                lambda item: item["status"]["state"] not in {"PENDING", "RUNNING"},
            )
        return result

    publication = sql(f"SELECT run_id FROM {catalog}.gold.latest_publication")
    if publication["status"]["state"] != "SUCCEEDED" or len(
        publication.get("result", {}).get("data_array", [])
    ) != 1:
        raise RuntimeError("Expected exactly one published run")
    for table in ("silver.fields", "eval.answer_key"):
        result = sql(f"SELECT 1 FROM {catalog}.{table} LIMIT 1")
        error = result["status"].get("error", {})
        if result["status"]["state"] != "FAILED" or error.get("error_code") not in {
            "PERMISSION_DENIED", "INSUFFICIENT_PERMISSIONS",
        }:
            raise RuntimeError(f"Expected an explicit permission denial for {table}")
    print("Processing job, Gold publication, and Silver/eval denial checks passed")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Databricks HTTP request failed with status {error.code}") from None
