"""Reconcile one LedgerLens environment with administrator CLI profiles."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from fixtures import bootstrap_fixture


ROOT = Path(__file__).resolve().parents[1]
SERVING = (
    "gold.latest_publication", "gold.current_person_year", "gold.current_missing_documents",
    "gold.current_prefill", "gold.current_lineage", "gold.current_review_summary",
    "gold.current_extraction_accuracy", "gold.current_document_state",
)
SNAPSHOTS = (
    "silver.fields", "gold.person_year", "gold.missing_documents", "gold.prefill",
    "gold.lineage", "gold.review_summary", "gold.extraction_accuracy",
)
LEDGERS = (
    "bronze.documents", "bronze.extractions", "review.decisions", "ops.pipeline_runs",
    "ops.publications", "ops.document_releases",
)
PROCESSOR_OUTPUTS = (
    "bronze.documents", "bronze.extractions", "ops.pipeline_runs", "ops.publications",
    "ops.document_releases", *(table for table in SNAPSHOTS if table != "gold.extraction_accuracy"),
)


def identifier(value):
    return "`" + value.replace("`", "``") + "`"


def ddl(catalog):
    for filename in ("schema.sql", "state.sql"):
        sql = ROOT.joinpath("provisioning", filename).read_text()
        for statement in sql.format(catalog=catalog).split(";"):
            if statement.strip():
                yield statement.strip()

    for table in (*LEDGERS, *SNAPSHOTS):
        yield f"ALTER TABLE {catalog}.{table} SET TBLPROPERTIES ('delta.appendOnly' = 'true')"
    yield f"""CREATE OR REPLACE VIEW {catalog}.gold.latest_publication AS
SELECT run_id, publication_seq, published_at FROM {catalog}.ops.publications
ORDER BY publication_seq DESC LIMIT 1"""
    for table in SNAPSHOTS:
        if not table.startswith("gold."):
            continue
        name = table.split(".")[1]
        yield f"""CREATE OR REPLACE VIEW {catalog}.gold.current_{name} AS
SELECT snapshot.* FROM {catalog}.{table} AS snapshot
JOIN {catalog}.ops.publications AS publication ON snapshot.run_id = publication.run_id"""


def grants(catalog, principal, *, reads=(), writes=(), volumes=()):
    subject = identifier(principal)
    yield f"GRANT USE CATALOG ON CATALOG {catalog} TO {subject}"
    objects = (*reads, *writes, *volumes)
    for schema in sorted({name.split(".")[0] for name in objects}):
        yield f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO {subject}"
    for table in reads:
        yield f"GRANT SELECT ON TABLE {catalog}.{table} TO {subject}"
    for table in writes:
        yield f"GRANT MODIFY ON TABLE {catalog}.{table} TO {subject}"
    for volume in volumes:
        yield f"GRANT READ VOLUME ON VOLUME {catalog}.{volume} TO {subject}"


def dbx(profile, *command, payload=None, response=True):
    args = ["databricks", *command, "--profile", profile, "--output", "json"]
    if payload is not None:
        args.extend(("--json", json.dumps(payload)))
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
    if response and result.stdout.strip():
        return json.loads(result.stdout)
    return None


def execute_sql(profile, warehouse_id, statement, parameters=()):
    payload = {"warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "30s"}
    if parameters:
        payload["parameters"] = [
            {"name": name, "type": sql_type, "value": str(value)}
            for name, sql_type, value in parameters
        ]
    result = dbx(profile, "api", "post", "/api/2.0/sql/statements", payload=payload)
    deadline = time.monotonic() + 300
    while result["status"]["state"] in {"PENDING", "RUNNING"}:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"SQL statement still running: {result['statement_id']}")
        time.sleep(2)
        result = dbx(profile, "api", "get", f"/api/2.0/sql/statements/{result['statement_id']}")
    if result["status"]["state"] != "SUCCEEDED":
        error = result["status"].get("error", {})
        raise RuntimeError(f"SQL failed for {statement.splitlines()[0]}: {error.get('message')}")


def find(profile, resource, attribute, value):
    matches = dbx(profile, resource, "list", "--filter", f"{attribute} eq {json.dumps(value)}")
    if matches is None:
        matches = []
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one workspace {resource} for {value}")
    return matches[0] if matches else None


def configure_entitlements(profile, principal, *, sql_client):
    details = dbx(profile, "service-principals", "get", principal["id"])
    existing = {item["value"] for item in details.get("entitlements", [])}
    expected = {"workspace-access"}
    if sql_client:
        expected.add("databricks-sql-access")
    if missing := expected - existing:
        dbx(profile, "service-principals", "patch", principal["id"], payload={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "entitlements",
                            "value": [{"value": value} for value in sorted(missing)]}],
        })


def create_reviewer_group(args, ron):
    profile = args.workspace_profile
    name = f"ledgerlens-{args.environment}-reviewers"
    group = find(profile, "groups", "displayName", name)
    if group is None:
        group = dbx(profile, "groups", "create", payload={"displayName": name})
    group = dbx(profile, "groups", "get", group["id"])
    if str(ron["id"]) not in {str(member["value"]) for member in group.get("members", [])}:
        dbx(profile, "groups", "patch", group["id"], payload={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "members", "value": [{"value": str(ron["id"])}]}],
        })


def seed_fixture(args, catalog, sql):
    pdf, entry = bootstrap_fixture()
    with tempfile.TemporaryDirectory(prefix="ledgerlens-fixture-") as temp:
        directory = Path(temp)
        document = directory / entry["filename"]
        document.write_bytes(pdf)
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps([entry], indent=2) + "\n")
        dbx(args.workspace_profile, "fs", "cp", str(document),
            f"dbfs:/Volumes/{catalog}/bronze/inbox/{entry['filename']}", "--overwrite", response=False)
        dbx(args.workspace_profile, "fs", "cp", str(manifest),
            f"dbfs:/Volumes/{catalog}/eval/fixtures/manifest.json", "--overwrite", response=False)

    for form in entry["expected_forms"]:
        sql(f"""MERGE INTO {catalog}.bronze.expected_forms AS target
USING (SELECT :person_key AS person_key, :tax_year AS tax_year, :form AS expected_form) AS source
ON target.person_key = source.person_key AND target.tax_year = source.tax_year
   AND target.expected_form = source.expected_form
WHEN NOT MATCHED THEN INSERT *""",
            (("person_key", "STRING", entry["person_key"]), ("tax_year", "INT", entry["tax_year"]),
             ("form", "STRING", form)))

    for field in entry["fields"]:
        keys = ("fixture_id", "doc_id", "field_id", "field_name", "expected_value", "variant", "form_type")
        values = entry | field | {"doc_id": entry["sha256"]}
        columns = ", ".join(f":{key} AS {key}" for key in keys)
        sql(f"""MERGE INTO {catalog}.eval.answer_key AS target
USING (SELECT {columns}) AS source
ON target.fixture_id = source.fixture_id AND target.field_id = source.field_id
WHEN NOT MATCHED THEN INSERT *""", [(key, "STRING", values[key]) for key in keys])


def provision(args):
    catalog = "ledgerlens_" + args.environment
    prefix = "ledgerlens-" + args.environment
    ron = dbx(args.workspace_profile, "current-user", "me")
    if ron["userName"] != args.ron_user:
        raise RuntimeError("Authenticate the radkay-lab workspace CLI profile as Ron")
    principals = {}
    integration = "ci" if args.environment == "dev" else "evidence"
    for role in ("processor", "evaluator", integration, "reader"):
        principal = find(args.workspace_profile, "service-principals", "displayName", f"{prefix}-{role}")
        if principal is None or principal.get("active") is False:
            raise RuntimeError(f"Create and activate the radkay-lab workspace identity {prefix}-{role}")
        principals["integration" if role == integration else role] = principal
    warehouse = dbx(args.workspace_profile, "warehouses", "get", args.warehouse_id)
    if warehouse.get("cluster_size") != "2X-Small":
        raise RuntimeError("Specify the existing shared 2X-Small warehouse")
    for role, principal in principals.items():
        configure_entitlements(args.workspace_profile, principal, sql_client=role in {"integration", "reader"})
    create_reviewer_group(args, ron)

    apps = {role: principal["applicationId"] for role, principal in principals.items()}

    def sql(statement, parameters=()):
        execute_sql(args.workspace_profile, args.warehouse_id, statement, parameters)

    for statement in ddl(catalog):
        sql(statement)

    seed_fixture(args, catalog, sql)
    access = (
        (apps["processor"], {
            "reads": (*PROCESSOR_OUTPUTS, "bronze.expected_forms", "review.decisions", "review.reason_codes"),
            "writes": PROCESSOR_OUTPUTS, "volumes": ("bronze.inbox",),
        }),
        (apps["evaluator"], {
            "reads": ("eval.answer_key", "bronze.documents", "bronze.extractions", "ops.publications",
                      "gold.extraction_accuracy"),
            "writes": ("gold.extraction_accuracy",), "volumes": ("eval.fixtures",),
        }),
        (apps["integration"], {"reads": (*SERVING, "ops.pipeline_runs")}),
        (apps["reader"], {"reads": SERVING}),
        (args.ron_user, {
            "reads": ("gold.current_document_state", "review.current_workbench", "review.reason_codes",
                      "ops.pipeline_runs"),
            "writes": ("review.decisions",), "volumes": ("bronze.inbox",),
        }),
    )
    for principal, privileges in access:
        for statement in grants(catalog, principal, **privileges):
            sql(statement)

    warehouse_acl = dbx(args.workspace_profile, "permissions", "get", "warehouses", args.warehouse_id)
    updates = [
        {"service_principal_name": apps[role], "permission_level": "CAN_USE"}
        for role in ("integration", "reader")
    ]
    if not any(
        entry.get("user_name") == args.ron_user
        and any(grant["permission_level"] in {"CAN_USE", "CAN_MANAGE", "IS_OWNER"}
                for grant in entry["all_permissions"])
        for entry in warehouse_acl.get("access_control_list", [])
    ):
        updates.append({"user_name": args.ron_user, "permission_level": "CAN_USE"})
    dbx(args.workspace_profile, "permissions", "update", "warehouses", args.warehouse_id,
        payload={"access_control_list": updates})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=("dev", "demo"))
    parser.add_argument("--plan", action="store_true", help="Print Unity Catalog DDL without connecting")
    for name in ("workspace-profile", "warehouse-id", "ron-user"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args()
    if args.plan:
        print(";\n\n".join(ddl("ledgerlens_" + args.environment)) + ";")
        return
    for name in ("workspace_profile", "warehouse_id", "ron_user"):
        if not getattr(args, name):
            parser.error(f"--{name.replace('_', '-')} is required when applying")
    if not re.fullmatch(r"[0-9a-f]+", args.warehouse_id):
        parser.error("Specify the hexadecimal SQL warehouse ID")
    try:
        provision(args)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Databricks command failed: {error.cmd[1:3]}\n{error.stderr or ''}") from None


if __name__ == "__main__":
    main()
