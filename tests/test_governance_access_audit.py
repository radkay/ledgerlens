"""Offline grant-matrix tests and a read-only administrator ACL audit."""

import argparse
from collections import defaultdict
import json
import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import call, patch
from urllib.parse import urlencode

from provisioning import provision


SCHEMAS = ("bronze", "silver", "gold", "review", "eval", "ops")
GOLD_TABLES = (
    "person_year", "missing_documents", "prefill", "lineage", "review_summary",
    "confidence_summary", "extraction_accuracy",
)
OUTPUTS = (
    "bronze.documents", "bronze.extractions", "silver.fields", "ops.pipeline_runs",
    "ops.publications", "ops.document_releases",
    *(f"gold.{name}" for name in GOLD_TABLES if name != "extraction_accuracy"),
)


def policy(environment, *, history, identifiers, hosted_app):
    prefixes = ("current", "published") if history else ("current",)
    serving = (
        "gold.latest_publication", "gold.current_document_state",
        *(f"gold.{prefix}_{name}" for prefix in prefixes for name in GOLD_TABLES),
    )
    roles = {
        "processor": {"SELECT": (*OUTPUTS, "bronze.expected_forms", "review.decisions", "review.reason_codes"),
                      "MODIFY": OUTPUTS, "READ_VOLUME": ("bronze.inbox", "review.masked_previews"),
                      "WRITE_VOLUME": ("review.masked_previews",)},
        "evaluator": {"SELECT": ("eval.answer_key", "bronze.documents", "bronze.extractions",
                                "ops.publications", "gold.extraction_accuracy"),
                      "MODIFY": ("gold.extraction_accuracy",), "READ_VOLUME": ("eval.fixtures",)},
        "integration": {"SELECT": (*serving, "ops.pipeline_runs")},
        "reader": {"SELECT": serving},
        "reviewers": {"SELECT": ("gold.current_document_state", "review.current_workbench",
                                 "review.reason_codes", "ops.pipeline_runs"),
                      "MODIFY": ("review.decisions",), "READ_VOLUME": ("review.masked_previews",)},
        "operator": {"READ_VOLUME": ("bronze.inbox",)},
    }
    if identifiers:
        roles["identifier-readers"] = {"SELECT": ("silver.fields",)}
    if hosted_app and environment == "demo":
        roles["app"] = {"SELECT": serving}
    return roles


def expected_grants(roles):
    by_object = defaultdict(dict)
    for role, grants in roles.items():
        by_object["catalog", ""][role] = {"USE_CATALOG"}
        for privilege, names in grants.items():
            kind = "volume" if privilege.endswith("_VOLUME") else "table"
            for name in names:
                by_object[kind, name].setdefault(role, set()).add(privilege)
                by_object["schema", name.split(".")[0]][role] = {"USE_SCHEMA"}
    return by_object


def read_grants(profile, securable, full_name):
    assignments = defaultdict(lambda: defaultdict(set))
    flags = ["--max-results", "0"]
    while True:
        page = provision.dbx(profile, "grants", "get-effective", securable, full_name, *flags) or {}
        for row in page.get("privilege_assignments", []):
            for grant in row.get("privileges", []):
                scope = "inherited" if grant.get("inherited_from_type") else "direct"
                assignments[row["principal"]][scope].add(grant["privilege"])
        if not (token := page.get("next_page_token")):
            return assignments
        flags = ["--max-results", "0", "--page-token", token]


def find_group(profile, group_name):
    path = "/api/2.0/account/scim/v2/Groups"
    query = urlencode({"filter": f"displayName eq {json.dumps(group_name)}"})
    groups = provision.dbx(profile, "api", "get", f"{path}?{query}").get("Resources", [])
    if len(groups) != 1:
        raise RuntimeError(f"Expected exactly one account group named {group_name}")
    return group_name, groups[0]["id"]


def parent_group_ids(profile, principal_id):
    path = f"/api/2.0/identity/principals/{principal_id}/transitive-parent-groups"
    ids = set()
    query = ""
    while True:
        page = provision.dbx(profile, "api", "get", path + query)
        ids.update(str(group["group_id"]) for group in page.get("transitive_parent_groups", []))
        if not (token := page.get("next_page_token")):
            return ids
        query = "?" + urlencode({"page_token": token})


def resolve_identities(args, ron, environment, roles):
    profile = args.workspace_profile
    prefix = f"ledgerlens-{environment}-"
    result = {"operator": (args.ron_user, ron["id"])}
    for role in roles.keys() - result.keys():
        if role in {"reviewers", "identifier-readers"}:
            result[role] = find_group(profile, prefix + role)
            continue
        if role == "app":
            _, client_id = provision.resolve_demo_app(profile)
            attribute, value = "applicationId", client_id
        else:
            suffix = {"dev": "ci", "demo": "evidence"}[environment] if role == "integration" else role
            attribute, value = "displayName", prefix + suffix
        principal = provision.find(profile, "service-principals", attribute, value)
        if not principal or principal.get("active") is False:
            raise RuntimeError(f"Missing active identity {environment}/{role}")
        result[role] = (principal["applicationId"], principal["id"])
    return result


def compare_grants(expected, measured, subjects, parents):
    problems = []
    for label, (principal, _) in subjects.items():
        own = measured.get(principal, {})
        actual = own.get("direct", set())
        if label[1] != "operator":
            actual = actual | own.get("inherited", set())
            for group in parents.get(principal, ()):
                grants = measured.get(group, {})
                actual = actual | grants.get("direct", set()) | grants.get("inherited", set())
        needed = expected.get(label, set())
        for disposition, privileges in (("missing", needed - actual), ("unexpected", actual - needed)):
            if privileges:
                problems.append(f"{'/'.join(label)} {disposition} {', '.join(sorted(privileges))}")
    return problems


def audit_catalog(args, environment, roles, subjects, parents):
    catalog = f"ledgerlens_{environment}"
    expected = expected_grants(roles)
    objects = set(expected) | {("schema", schema) for schema in SCHEMAS} | {
        ("table", "review.document_state"), ("function", "silver.mask_identifier"),
    }
    if "identifier-readers" in roles:
        objects |= {("function", "silver.is_identifier_field"), ("function", "silver.mask_workbench_identifier")}
    problems = []
    for kind, name in sorted(objects):
        full_name = f"{catalog}.{name}" if name else catalog
        measured = read_grants(args.workspace_profile, kind, full_name)
        desired = {(environment, role): grants for role, grants in expected[kind, name].items()}
        problems.extend(
            f"{full_name}: {problem}"
            for problem in compare_grants(desired, measured, subjects, parents)
        )
    problems.extend(audit_table_controls(args.workspace_profile, catalog, roles))
    return problems


def audit_table_controls(profile, catalog, roles):
    writable = {name for grants in roles.values() for name in grants.get("MODIFY", ())}
    problems = []
    for table in sorted(writable):
        full_name = f"{catalog}.{table}"
        details = provision.dbx(profile, "tables", "get", full_name)
        if (details.get("data_source_format") != "DELTA"
                or details.get("properties", {}).get("delta.appendOnly") != "true"):
            problems.append(f"{full_name}: expected an append-only Delta table")
        if table == "silver.fields":
            masks = {column["name"]: column.get("mask") or {} for column in details.get("columns", [])}
            for column in ("extracted_value", "final_value"):
                mask = masks.get(column, {})
                if (mask.get("function_name") != f"{catalog}.silver.mask_identifier"
                        or mask.get("using_column_names") != ["field_name"]):
                    problems.append(f"{full_name}.{column}: expected the catalog identifier mask")
    return problems


def explicit_acl(response):
    explicit = defaultdict(set)
    for entry in response.get("access_control_list", []):
        for kind in ("group_name", "service_principal_name"):
            if kind in entry:
                explicit[entry[kind]].update(
                    grant["permission_level"] for grant in entry["all_permissions"] if not grant.get("inherited")
                )
    return explicit


def audit_warehouse(args, subjects):
    acl = explicit_acl(provision.dbx(args.workspace_profile, "permissions", "get", "warehouses",
                                     args.warehouse_id))
    problems = []
    for (environment, role), (principal, _) in subjects.items():
        can_use = role in {"integration", "reader", "reviewers", "identifier-readers", "app"}
        actual = acl[principal]
        if role != "operator" and actual != ({"CAN_USE"} if can_use else set()):
            problems.append(f"warehouse: {environment}/{role} explicit permissions are {sorted(actual)!r}")
    return problems


def audit_app(args, subjects):
    acl = explicit_acl(provision.dbx(args.workspace_profile, "apps", "get-permissions", provision.DEMO_APP_NAME))
    problems = []
    for (environment, role), (principal, _) in subjects.items():
        expected = {"CAN_USE"} if (environment, role) == ("demo", "reviewers") else set()
        # The App's own principal is Databricks-managed. This ACL governs interactive users.
        if role not in {"operator", "app"} and acl[principal] != expected:
            problems.append(f"demo App: {environment}/{role} explicit permissions are {sorted(acl[principal])!r}")
    return problems


def live_audit(args):
    ron = provision.require_ron(args)
    provision.require_shared_warehouse(args)
    roles = {
        env: policy(env, history=env in args.published_history, identifiers=env in args.identifier_readers,
                    hosted_app=args.hosted_app)
        for env in ("dev", "demo")
    }
    subjects = {
        (env, role): identity for env in roles
        for role, identity in resolve_identities(args, ron, env, roles[env]).items()
    }
    non_operators = [principal for (_, role), (principal, _) in subjects.items() if role != "operator"]
    if len(non_operators) != len(set(non_operators)):
        raise RuntimeError("Expected separate workload identities and groups for dev and demo")

    profile = args.workspace_profile
    known_groups = {
        str(identifier): principal
        for (_, role), (principal, identifier) in subjects.items()
        if role in {"reviewers", "identifier-readers"}
    }
    parents = {}
    operator_groups = parent_group_ids(profile, ron["id"])
    problems = []
    for env in roles:
        _, reviewers_id = subjects[env, "reviewers"]
        if str(reviewers_id) not in operator_groups:
            problems.append(f"{env}/operator lacks reviewer group membership")
    for label, (principal, identifier) in subjects.items():
        if label[1] == "operator":
            continue
        groups = parent_group_ids(profile, identifier)
        for group_id in groups - known_groups.keys():
            group = provision.dbx(profile, "api", "get", f"/api/2.0/identity/groups/{group_id}")
            known_groups[group_id] = group["group_name"]
        parents[principal] = {known_groups[group_id] for group_id in groups}

    for env in roles:
        # The same administrator has an inbox grant in each environment.
        scoped = {label: identity for label, identity in subjects.items() if label[1] != "operator" or label[0] == env}
        problems.extend(audit_catalog(args, env, roles[env], scoped, parents))
    problems.extend(audit_warehouse(args, subjects))
    if args.hosted_app:
        problems.extend(audit_app(args, subjects))
    return problems


def live_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-profile", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--ron-user", required=True)
    parser.add_argument("--published-history", nargs="+", choices=("dev", "demo"), default=(),
                        help="Catalogs where the published history views have been applied")
    parser.add_argument("--identifier-readers", nargs="+", choices=("dev", "demo"), default=(),
                        help="Catalogs where identifier reader governance has been applied")
    parser.add_argument("--hosted-app", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]+", args.warehouse_id):
        parser.error("Specify the hexadecimal SQL warehouse ID")
    problems = live_audit(args)
    if problems:
        raise SystemExit("\n".join(problems))
    print("Dev and demo Unity Catalog and explicit warehouse ACL audit passed.")


class GovernanceAuditTests(unittest.TestCase):
    def test_expected_matrix_allows_scoped_access_and_denies_extras_for_every_role(self):
        for env in ("dev", "demo"):
            roles = policy(env, history=True, identifiers=True, hosted_app=True)
            grants = expected_grants(roles)
            self.assertEqual("app" in roles, env == "demo")
            self.assertEqual(set(grants["table", "gold.published_confidence_summary"]),
                             {"app", "reader", "integration"} & roles.keys())
            self.assertEqual(set(grants["table", "eval.answer_key"]), {"evaluator"})
            self.assertEqual(set(grants["table", "silver.fields"]), {"processor", "identifier-readers"})
            self.assertEqual(set(grants["table", "review.current_workbench"]), {"reviewers"})
            self.assertEqual(grants["volume", "review.masked_previews"],
                             {"processor": {"READ_VOLUME", "WRITE_VOLUME"}, "reviewers": {"READ_VOLUME"}})
            subjects = {(env, role): (role, "1") for role in roles}
            for (kind, name), desired in grants.items():
                expected = {(env, role): privileges for role, privileges in desired.items()}
                measured = {role: {"direct": privileges} for role, privileges in desired.items()}
                with self.subTest(environment=env, kind=kind, name=name):
                    self.assertEqual(compare_grants(expected, measured, subjects, {}), [])
                    for role in roles:
                        if role in measured:
                            missing = measured.copy()
                            del missing[role]
                            self.assertIn(f"{env}/{role} missing",
                                          "\n".join(compare_grants(expected, missing, subjects, {})))
                        extra = measured | {role: {"direct": desired.get(role, set()) | {"ALL_PRIVILEGES"}}}
                        self.assertIn(f"{env}/{role} unexpected ALL_PRIVILEGES",
                                      compare_grants(expected, extra, subjects, {}))

    def test_denies_cross_catalog_group_inheritance_without_using_admin_rights(self):
        subjects = {("dev", "reader"): ("dev-reader", "1"), ("demo", "operator"): ("operator", "2")}
        measured = {"foreign-group": {"inherited": {"SELECT"}}, "operator": {"inherited": {"SELECT"}}}
        self.assertEqual(compare_grants({}, measured, subjects, {"dev-reader": {"foreign-group"}}),
                         ["dev/reader unexpected SELECT"])

    def test_acl_paging_continues_through_empty_pages(self):
        with patch.object(provision, "dbx", side_effect=[
            {"next_page_token": "next"}, {"privilege_assignments": [
                {"principal": "group", "privileges": [
                    {"privilege": "SELECT", "inherited_from_type": "schema"},
                    {"privilege": "MODIFY"},
                ]},
            ]},
        ]) as dbx:
            self.assertEqual(read_grants("profile", "table", "ledgerlens_dev.gold.person_year"),
                             {"group": {"direct": {"MODIFY"}, "inherited": {"SELECT"}}})
        self.assertEqual(dbx.call_args.args[-2:], ("--page-token", "next"))

    def test_demo_app_resolution_and_reader_only_patch(self):
        args = SimpleNamespace(environment="demo", workspace_profile="profile", warehouse_id="dead",
                               ron_user="operator@example.invalid")
        application_id = "12121212-3434-5656-7878-909090909090"
        app = {"name": "ledgerlens-demo-app", "service_principal_client_id": application_id}
        output = StringIO()
        with (
            redirect_stdout(output),
            patch.object(provision, "require_ron"),
            patch.object(provision, "require_shared_warehouse"),
            patch.object(provision, "dbx", side_effect=[
                app, *({} for _ in range(2 + len(provision.SERVING))), {}, {},
            ]) as dbx,
        ):
            provision.grant_demo_app_reader(args)
        self.assertEqual(json.loads(output.getvalue()), {
            "app": "ledgerlens-demo-app", "service_principal_client_id": application_id,
        })
        self.assertEqual(dbx.call_args_list[0], call("profile", "apps", "get", "ledgerlens-demo-app"))
        configured_views = policy("demo", history="gold.published_person_year" in provision.SERVING,
                                  identifiers=False, hosted_app=True)["app"]["SELECT"]
        expected = {
            ("catalog", "ledgerlens_demo", "USE_CATALOG"),
            ("schema", "ledgerlens_demo.gold", "USE_SCHEMA"),
            *(("table", f"ledgerlens_demo.{view}", "SELECT") for view in configured_views),
        }
        self.assertCountEqual(dbx.call_args_list[1:-2], [
            call("profile", "grants", "update", kind, name,
                 payload={"changes": [{"principal": application_id, "add": [privilege]}]})
            for kind, name, privilege in expected
        ])
        self.assertEqual(dbx.call_args_list[-2:], [
            call("profile", "permissions", "get", "warehouses", "dead"),
            call("profile", "permissions", "update", "warehouses", "dead", payload={"access_control_list": [
                {"service_principal_name": application_id, "permission_level": "CAN_USE"},
            ]}),
        ])

    def test_audit_detects_missing_append_only_or_silver_mask(self):
        roles = {"processor": {"MODIFY": ("silver.fields",)}, "reviewers": {"MODIFY": ("review.decisions",)}}
        delta = {"data_source_format": "DELTA", "properties": {"delta.appendOnly": "true"}}
        mask = {"function_name": "ledgerlens_dev.silver.mask_identifier", "using_column_names": ["field_name"]}
        silver = delta | {"columns": [
            {"name": "extracted_value", "mask": mask}, {"name": "final_value", "mask": mask},
        ]}
        with patch.object(provision, "dbx", side_effect=[delta, silver]):
            self.assertEqual(audit_table_controls("profile", "ledgerlens_dev", roles), [])
        silver["columns"][1]["mask"] = mask | {"function_name": "ledgerlens_demo.silver.mask_identifier"}
        with patch.object(provision, "dbx", side_effect=[{"data_source_format": "DELTA"}, silver]):
            self.assertEqual(audit_table_controls("profile", "ledgerlens_dev", roles), [
                "ledgerlens_dev.review.decisions: expected an append-only Delta table",
                "ledgerlens_dev.silver.fields.final_value: expected the catalog identifier mask",
            ])

    def test_workspace_acls_limit_explicit_access_to_intended_identities(self):
        args = SimpleNamespace(workspace_profile="profile", warehouse_id="dead")
        subjects = {
            ("dev", "reader"): ("dev-reader", "1"), ("demo", "processor"): ("demo-processor", "2"),
            ("demo", "reviewers"): ("demo-reviewers", "3"), ("demo", "app"): ("app", "4"),
        }
        def entry(kind, principal, level, *, inherited=False):
            return {kind: principal, "all_permissions": [{"permission_level": level, "inherited": inherited}]}

        warehouse = {"access_control_list": [
            entry("service_principal_name", "dev-reader", "CAN_USE"),
            entry("service_principal_name", "demo-processor", "CAN_USE", inherited=True),
            entry("group_name", "demo-reviewers", "CAN_USE"),
            entry("service_principal_name", "app", "CAN_USE"),
        ]}
        app = {"access_control_list": [entry("group_name", "demo-reviewers", "CAN_USE")]}
        with patch.object(provision, "dbx", side_effect=[warehouse, app]):
            self.assertEqual(audit_warehouse(args, subjects), [])
            self.assertEqual(audit_app(args, subjects), [])
        app["access_control_list"].append(entry("service_principal_name", "dev-reader", "CAN_USE"))
        with patch.object(provision, "dbx", return_value=app):
            self.assertEqual(audit_app(args, subjects), ["demo App: dev/reader explicit permissions are ['CAN_USE']"])

    def test_app_resolution_fails_closed_and_dev_is_denied(self):
        with patch.object(provision, "dbx", return_value={
            "name": "ledgerlens-demo-app", "service_principal_client_id": None,
        }) as dbx:
            with self.assertRaisesRegex(RuntimeError, "scoped to demo"):
                provision.grant_demo_app_reader(SimpleNamespace(environment="dev"))
            dbx.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "host-managed"):
                provision.resolve_demo_app("profile")


if __name__ == "__main__":
    live_main()
