"""Reconcile one LedgerLens environment with administrator CLI profiles."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlencode

if __package__:
    from .fixtures import bootstrap_fixture
else:
    from fixtures import bootstrap_fixture


ROOT = Path(__file__).resolve().parents[1]
GOLD_SNAPSHOTS = (
    "person_year", "missing_documents", "prefill", "lineage", "review_summary",
    "confidence_summary", "extraction_accuracy",
)
SNAPSHOTS = ("silver.fields", *(f"gold.{name}" for name in GOLD_SNAPSHOTS))
SERVING = (
    "gold.latest_publication",
    *(f"gold.{prefix}_{name}" for name in GOLD_SNAPSHOTS for prefix in ("current", "published")),
    "gold.current_document_state",
)
LEDGERS = (
    "bronze.documents", "bronze.extractions", "review.decisions", "ops.pipeline_runs",
    "ops.publications", "ops.document_releases",
)
PROCESSOR_OUTPUTS = (
    "bronze.documents", "bronze.extractions", "ops.pipeline_runs", "ops.publications",
    "ops.document_releases", *(table for table in SNAPSHOTS if table != "gold.extraction_accuracy"),
)
WORKBENCH_READS = (
    "gold.current_document_state", "review.current_workbench", "review.reason_codes", "ops.pipeline_runs",
)
WORKBENCH_WRITES = ("review.decisions",)
MASKED_PREVIEWS = "review.masked_previews"


def identifier(value):
    return "`" + value.replace("`", "``") + "`"


def view_ddl(name, query):
    # ALTER VIEW keeps grants made to existing views, including the hosted App reader.
    yield f"CREATE VIEW IF NOT EXISTS {name} AS\n{query}"
    yield f"ALTER VIEW {name} AS\n{query}"


def ddl(catalog):
    for filename in ("schema.sql", "state.sql"):
        sql = ROOT.joinpath("provisioning", filename).read_text()
        for statement in sql.format(catalog=catalog).split(";"):
            statement = statement.strip()
            if statement.startswith("CREATE OR REPLACE VIEW "):
                name, query = statement.removeprefix("CREATE OR REPLACE VIEW ").split(" AS\n", 1)
                yield from view_ddl(name, query)
            elif statement:
                yield statement

    for table in (*LEDGERS, *SNAPSHOTS):
        yield f"ALTER TABLE {catalog}.{table} SET TBLPROPERTIES ('delta.appendOnly' = 'true')"
    yield from view_ddl(f"{catalog}.gold.latest_publication", f"""
SELECT run_id, publication_seq, published_at FROM {catalog}.ops.publications
ORDER BY publication_seq DESC LIMIT 1""".strip())
    for name in GOLD_SNAPSHOTS:
        for prefix, publication in (("published", "ops.publications"), ("current", "gold.latest_publication")):
            yield from view_ddl(f"{catalog}.gold.{prefix}_{name}", f'''
SELECT snapshot.* FROM {catalog}.gold.{name} AS snapshot
JOIN {catalog}.{publication} AS publication ON snapshot.run_id = publication.run_id'''.strip())


def grants(catalog, principal, *, reads=(), writes=(), volume_reads=(), volume_writes=()):
    subject = identifier(principal)
    yield f"GRANT USE CATALOG ON CATALOG {catalog} TO {subject}"
    objects = (*reads, *writes, *volume_reads, *volume_writes)
    for schema in sorted({name.split(".")[0] for name in objects}):
        yield f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO {subject}"
    for table in reads:
        yield f"GRANT SELECT ON TABLE {catalog}.{table} TO {subject}"
    for table in writes:
        yield f"GRANT MODIFY ON TABLE {catalog}.{table} TO {subject}"
    for volume in volume_reads:
        yield f"GRANT READ VOLUME ON VOLUME {catalog}.{volume} TO {subject}"
    for volume in volume_writes:
        yield f"GRANT WRITE VOLUME ON VOLUME {catalog}.{volume} TO {subject}"


def revoke_direct_workbench(catalog, principal):
    subject = identifier(principal)
    for table in WORKBENCH_READS:
        yield f"REVOKE SELECT ON TABLE {catalog}.{table} FROM {subject}"
    for table in WORKBENCH_WRITES:
        yield f"REVOKE MODIFY ON TABLE {catalog}.{table} FROM {subject}"
    for schema in sorted({table.split(".")[0] for table in (*WORKBENCH_READS, *WORKBENCH_WRITES)}):
        yield f"REVOKE USE SCHEMA ON SCHEMA {catalog}.{schema} FROM {subject}"


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
    groups_path = "/api/2.0/account/scim/v2/Groups"
    query = urlencode({"filter": f"displayName eq {json.dumps(name)}"})
    matches = dbx(profile, "api", "get", f"{groups_path}?{query}").get("Resources", [])
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one Databricks account group named {name}")
    group = matches[0] if matches else None
    workspace_group = find(profile, "groups", "displayName", name)
    if workspace_group and (group is None or str(workspace_group["id"]) != str(group["id"])):
        raise RuntimeError(
            f"Rename the legacy workspace-local group {name} and review its memberships "
            "and App permissions before assigning them to the replacement account group, then rerun."
        )
    if group is None:
        group = dbx(profile, "api", "post", groups_path, payload={"displayName": name})
    group_path = f"{groups_path}/{group['id']}"
    group = dbx(profile, "api", "get", group_path)
    if str(ron["id"]) not in {str(member["value"]) for member in group.get("members", [])}:
        dbx(profile, "api", "patch", group_path, payload={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "members", "value": [{"value": str(ron["id"])}]}],
        })
    assign_reviewer_group(profile, int(group["id"]))
    return name


def assign_reviewer_group(profile, group_id):
    path = "/api/2.0/identity/workspace-assignments"
    entitlements = {"WORKSPACE_ACCESS", "DATABRICKS_SQL_ACCESS"}
    query = ""
    while True:
        page = dbx(profile, "api", "get", path + query)
        if any(str(item["principal_id"]) == str(group_id) for item in page.get("workspace_assignments", [])):
            assignment = dbx(profile, "api", "get", f"{path}/{group_id}")
            existing = set(assignment.get("entitlements", []))
            if entitlements - existing:
                dbx(profile, "api", "patch", f"{path}/{group_id}?update_mask=entitlements", payload={
                    "principal_id": group_id, "entitlements": sorted(existing | entitlements),
                })
            return
        if not (token := page.get("next_page_token")):
            break
        query = "?" + urlencode({"page_token": token})
    dbx(profile, "api", "post", path, payload={
        "principal_id": group_id, "entitlements": sorted(entitlements),
    })


def configure_warehouse(args, apps, reviewers):
    profile = args.workspace_profile
    target = ("warehouses", args.warehouse_id)
    acl = dbx(profile, "permissions", "get", *target).get("access_control_list", [])
    expected = {
        ("group_name", reviewers),
        *(("service_principal_name", apps[role]) for role in ("integration", "reader")),
    }
    direct = []
    legacy_grant = False
    for entry in acl:
        identity = next((key, entry[key]) for key in ("user_name", "group_name", "service_principal_name")
                        if key in entry)
        for grant in entry["all_permissions"]:
            level = grant["permission_level"]
            if identity == ("user_name", args.ron_user) and level == "CAN_USE" and not grant.get("inherited"):
                legacy_grant = True
                continue
            if level in {"CAN_USE", "CAN_MANAGE", "IS_OWNER"}:
                expected.discard(identity)
            if not grant.get("inherited"):
                direct.append({identity[0]: identity[1], "permission_level": level})
    updates = [{key: name, "permission_level": "CAN_USE"} for key, name in sorted(expected)]
    if legacy_grant:
        # PATCH cannot delete a direct ACL entry. Preserve all other direct entries.
        updated_identities = {(key, name) for update in updates for key, name in update.items()
                              if key != "permission_level"}
        direct = [entry for entry in direct if not any(entry.get(key) == name for key, name in updated_identities)]
        dbx(profile, "permissions", "set", *target, payload={"access_control_list": direct + updates})
    elif updates:
        dbx(profile, "permissions", "update", *target, payload={"access_control_list": updates})


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
    reviewers = create_reviewer_group(args, ron)
    for role, principal in principals.items():
        configure_entitlements(args.workspace_profile, principal, sql_client=role in {"integration", "reader"})

    apps = {role: principal["applicationId"] for role, principal in principals.items()}

    def sql(statement, parameters=()):
        execute_sql(args.workspace_profile, args.warehouse_id, statement, parameters)

    for statement in ddl(catalog):
        sql(statement)

    seed_fixture(args, catalog, sql)
    access = (
        (apps["processor"], {
            "reads": (*PROCESSOR_OUTPUTS, "bronze.expected_forms", "review.decisions", "review.reason_codes"),
            "writes": PROCESSOR_OUTPUTS, "volume_reads": ("bronze.inbox", MASKED_PREVIEWS),
            "volume_writes": (MASKED_PREVIEWS,),
        }),
        (apps["evaluator"], {
            "reads": ("eval.answer_key", "bronze.documents", "bronze.extractions", "ops.publications",
                      "gold.extraction_accuracy"),
            "writes": ("gold.extraction_accuracy",), "volume_reads": ("eval.fixtures",),
        }),
        (apps["integration"], {"reads": (*SERVING, "ops.pipeline_runs")}),
        (apps["reader"], {"reads": SERVING}),
        (reviewers, {
            "reads": WORKBENCH_READS, "writes": WORKBENCH_WRITES, "volume_reads": (MASKED_PREVIEWS,),
        }),
        (args.ron_user, {"volume_reads": ("bronze.inbox",)}),
    )
    for principal, privileges in access:
        for statement in grants(catalog, principal, **privileges):
            sql(statement)

    configure_warehouse(args, apps, reviewers)
    for statement in revoke_direct_workbench(catalog, args.ron_user):
        sql(statement)


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
