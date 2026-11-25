import json
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, call, patch

from provisioning import provision
from provisioning.fixtures import bootstrap_fixture, demo_fixtures


class ProvisioningTests(unittest.TestCase):
    def test_ddl_creates_confidence_and_preview_objects(self):
        for env in ("dev", "demo"):
            catalog = f"ledgerlens_{env}"
            statements = list(provision.ddl(catalog))
            table = f"{catalog}.gold.confidence_summary"
            view = f"{catalog}.gold.current_confidence_summary"
            self.assertIn(f"CREATE VOLUME IF NOT EXISTS {catalog}.review.masked_previews", statements)
            self.assertIn(
                f"CREATE TABLE IF NOT EXISTS {table} (\n"
                "  run_id STRING NOT NULL, form_type STRING NOT NULL, confidence_bucket STRING NOT NULL,\n"
                "  count BIGINT NOT NULL\n) USING DELTA",
                statements,
            )
            self.assertIn(f"ALTER TABLE {table} SET TBLPROPERTIES ('delta.appendOnly' = 'true')", statements)
            query = (
                f"SELECT snapshot.* FROM {table} AS snapshot\n"
                f"JOIN {catalog}.gold.latest_publication AS publication ON snapshot.run_id = publication.run_id"
            )
            self.assertIn(f"CREATE VIEW IF NOT EXISTS {view} AS\n{query}", statements)
            self.assertIn(f"ALTER VIEW {view} AS\n{query}", statements)
            self.assertFalse(any(sql.startswith("CREATE OR REPLACE VIEW") for sql in statements))

    def test_allow_and_deny_grant_matrix_for_both_catalogs(self):
        for env, deployer in (("dev", "ci"), ("demo", "evidence")):
            with self.subTest(env=env):
                self.assert_grant_matrix(env, deployer)

    def assert_grant_matrix(self, env, deployer):
        catalog = f"ledgerlens_{env}"
        group = f"ledgerlens-{env}-reviewers"
        identifiers = f"ledgerlens-{env}-identifier-readers"
        args = Namespace(environment=env, workspace_profile="synthetic", warehouse_id="dead",
                         ron_user="operator@example.invalid")
        principal_ids = ("processor", "evaluator", deployer, "reader")
        with (
            patch.object(provision, "dbx", side_effect=[
                {"userName": args.ron_user, "id": "1"}, {"cluster_size": "2X-Small"},
            ]),
            patch.object(provision, "find", side_effect=[
                {"applicationId": name} for name in principal_ids
            ]) as find,
            patch.object(provision, "create_reviewer_group", return_value=group),
            patch.object(provision, "ensure_account_group", return_value=("group-path", 54)) as account_group,
            patch.object(provision, "assign_sql_group") as group_assignment,
            patch.object(provision, "configure_entitlements") as entitlements,
            patch.object(provision, "configure_warehouse") as warehouse,
            patch.object(provision, "seed_fixture"),
            patch.object(provision, "execute_sql") as execute,
        ):
            provision.provision(args)

        self.assertEqual(find.call_args_list, [
            call("synthetic", "service-principals", "displayName", f"ledgerlens-{env}-{name}")
            for name in principal_ids
        ])
        self.assertEqual([item.kwargs["sql_client"] for item in entitlements.call_args_list],
                         [False, False, True, True])
        account_group.assert_called_once_with("synthetic", identifiers)
        group_assignment.assert_called_once_with("synthetic", 54)
        warehouse.assert_called_once_with(args, dict(zip(
            ("processor", "evaluator", "integration", "reader"), principal_ids, strict=True,
        )), (group, identifiers))

        outputs = (
            "bronze.documents", "bronze.extractions", "silver.fields", "gold.person_year",
            "gold.missing_documents", "gold.prefill", "gold.lineage", "gold.review_summary",
            "gold.confidence_summary", "ops.pipeline_runs", "ops.publications", "ops.document_releases",
        )
        serving = (
            "gold.latest_publication", "gold.current_person_year", "gold.current_missing_documents",
            "gold.current_prefill", "gold.current_lineage", "gold.current_review_summary",
            "gold.current_confidence_summary", "gold.current_extraction_accuracy",
            "gold.current_document_state", "gold.published_person_year", "gold.published_missing_documents",
            "gold.published_prefill", "gold.published_lineage", "gold.published_review_summary",
            "gold.published_confidence_summary", "gold.published_extraction_accuracy",
        )
        workbench = ("gold.current_document_state", "review.current_workbench",
                     "review.reason_codes", "ops.pipeline_runs")
        # Exact sets also deny grants to other identities, schemas, objects and environments.
        expected = {
            "processor": {
                "USE SCHEMA": ("bronze", "silver", "gold", "review", "ops"),
                "SELECT": (*outputs, "bronze.expected_forms", "review.decisions", "review.reason_codes"),
                "MODIFY": outputs,
                "READ VOLUME": ("bronze.inbox", "review.masked_previews"),
                "WRITE VOLUME": ("review.masked_previews",),
            },
            "evaluator": {
                "USE SCHEMA": ("eval", "bronze", "ops", "gold"),
                "SELECT": ("eval.answer_key", "bronze.documents", "bronze.extractions",
                           "ops.publications", "gold.extraction_accuracy"),
                "MODIFY": ("gold.extraction_accuracy",),
                "READ VOLUME": ("eval.fixtures",),
            },
            deployer: {"USE SCHEMA": ("gold", "ops"), "SELECT": (*serving, "ops.pipeline_runs")},
            "reader": {"USE SCHEMA": ("gold",), "SELECT": serving},
            group: {"USE SCHEMA": ("gold", "review", "ops"), "SELECT": workbench,
                    "MODIFY": ("review.decisions",), "READ VOLUME": ("review.masked_previews",)},
            identifiers: {"USE SCHEMA": ("silver",), "SELECT": ("silver.fields",)},
            args.ron_user: {"USE SCHEMA": ("bronze",), "READ VOLUME": ("bronze.inbox",)},
        }
        kinds = {"USE SCHEMA": "SCHEMA", "SELECT": "TABLE", "MODIFY": "TABLE",
                 "READ VOLUME": "VOLUME", "WRITE VOLUME": "VOLUME"}
        expected_grants = {
            f"GRANT USE CATALOG ON CATALOG {catalog} TO `{identity}`" for identity in expected
        } | {
            f"GRANT {privilege} ON {kinds[privilege]} {catalog}.{name} TO `{identity}`"
            for identity, privileges in expected.items()
            for privilege, names in privileges.items()
            for name in names
        }
        sql = [item.args[2] for item in execute.call_args_list]
        self.assertCountEqual([statement for statement in sql if statement.startswith("GRANT ")], expected_grants)
        expected_revokes = {
            *(f"REVOKE SELECT ON TABLE {catalog}.{table} FROM `{args.ron_user}`" for table in workbench),
            f"REVOKE MODIFY ON TABLE {catalog}.review.decisions FROM `{args.ron_user}`",
            *(f"REVOKE USE SCHEMA ON SCHEMA {catalog}.{schema} FROM `{args.ron_user}`"
              for schema in ("gold", "ops", "review")),
        }
        self.assertCountEqual(sql[-len(expected_revokes):], expected_revokes)
        self.assertIn(
            f"ALTER TABLE {catalog}.review.decisions SET TBLPROPERTIES ('delta.appendOnly' = 'true')",
            sql,
        )

    def test_fixture_seed_targets_the_selected_catalog_and_is_repeatable(self):
        for environment, fixtures, inventory_count in (
            ("dev", [bootstrap_fixture()], 2), ("demo", demo_fixtures(), 10),
        ):
            with self.subTest(environment=environment):
                catalog = f"ledgerlens_{environment}"
                args = Namespace(environment=environment, workspace_profile="synthetic")
                root = f"dbfs:/Volumes/{catalog}"
                manifest_path = f"{root}/eval/fixtures/manifest.json"
                uploads = []
                sql = Mock()

                def upload(profile, command, action, local, remote, overwrite, *, response):
                    self.assertEqual((profile, command, action, overwrite, response),
                                     ("synthetic", "fs", "cp", "--overwrite", False))
                    self.assertEqual(sql.call_count, 2 if len(uploads) <= len(fixtures) else 4)
                    uploads.append((remote, Path(local).read_bytes()))

                with patch.object(provision, "dbx", side_effect=upload):
                    provision.seed_fixture(args, catalog, sql)
                    provision.seed_fixture(args, catalog, sql)

                pdfs = [(f"{root}/bronze/inbox/{entry['filename']}", pdf) for pdf, entry in fixtures]
                batch_size = len(pdfs) + 1
                self.assertEqual(len(uploads), 2 * batch_size)
                self.assertEqual(uploads[:len(pdfs)], pdfs)
                self.assertEqual(uploads[:batch_size], uploads[batch_size:])
                destination, manifest = uploads[len(pdfs)]
                self.assertEqual(destination, manifest_path)
                entries = [entry for _, entry in fixtures]
                self.assertEqual(len(entries), 12 if environment == "demo" else 1)
                self.assertEqual(json.loads(manifest), entries)

                self.assertEqual(sql.call_count, 4)
                self.assertEqual(sql.call_args_list[:2], sql.call_args_list[2:])
                fixture_inventory = sorted({
                    (entry["person_key"], entry["tax_year"], form)
                    for entry in entries for form in entry["expected_forms"]
                })
                self.assertEqual(len(fixture_inventory), inventory_count)
                answers = [
                    (entry["fixture_id"], entry["sha256"], item["field_id"], item["field_name"],
                     item["expected_value"], entry["variant"], entry["form_type"])
                    for entry in entries for item in entry["fields"]
                ]
                self.assertEqual(len(answers), 36 if environment == "demo" else 3)
                self.assertEqual(len({(row[0], row[2]) for row in answers}), len(answers))
                self.assert_fixture_merge(
                    sql.call_args_list[0].args, f"{catalog}.bronze.expected_forms",
                    (("person_key", "STRING"), ("tax_year", "INT"), ("expected_form", "STRING")),
                    ("person_key", "tax_year", "expected_form"), fixture_inventory,
                )
                self.assert_fixture_merge(
                    sql.call_args_list[1].args, f"{catalog}.eval.answer_key",
                    tuple((name, "STRING") for name in (
                        "fixture_id", "doc_id", "field_id", "field_name", "expected_value", "variant", "form_type"
                    )),
                    ("fixture_id", "field_id"), answers,
                )

    def assert_fixture_merge(self, invocation, table, columns, keys, rows):
        statement, parameters = invocation
        self.assertTrue(statement.startswith(f"MERGE INTO {table} AS target\n"))
        self.assertIn("ON " + " AND ".join(f"target.{key} = source.{key}" for key in keys), statement)
        self.assertTrue(statement.endswith("WHEN NOT MATCHED THEN INSERT *"))
        self.assertEqual(parameters, tuple(
            (f"{name}_{index}", kind, value)
            for index, row in enumerate(rows)
            for (name, kind), value in zip(columns, row, strict=True)
        ))
        for parameter, _, _ in parameters:
            self.assertIn(f":{parameter}", statement)

    def test_demo_manifest_is_not_replaced_if_an_inbox_upload_fails(self):
        args = Namespace(environment="demo", workspace_profile="synthetic")
        with (
            patch.object(provision, "dbx", side_effect=[None, RuntimeError("upload interrupted")]) as dbx,
            self.assertRaisesRegex(RuntimeError, "upload interrupted"),
        ):
            provision.seed_fixture(args, "ledgerlens_demo", Mock())
        self.assertEqual(dbx.call_count, 2)
        for upload in dbx.call_args_list:
            self.assertIn("/ledgerlens_demo/bronze/inbox/", upload.args[4])

    def test_warehouse_never_replaces_acl_when_ron_has_direct_can_use(self):
        args = Namespace(workspace_profile="synthetic", warehouse_id="dead", ron_user="operator@example.invalid")
        apps = {"integration": "ci", "reader": "reader"}
        reviewers = "ledgerlens-dev-reviewers"
        existing = [
            {"user_name": args.ron_user, "all_permissions": [
                {"permission_level": "CAN_USE", "inherited": False},
            ]},
            {"group_name": "All workspace users", "all_permissions": [{"permission_level": "CAN_USE"}]},
            {"group_name": "ledgerlens-demo-reviewers", "all_permissions": [{"permission_level": "CAN_USE"}]},
            {"service_principal_name": "reader", "all_permissions": [{"permission_level": "CAN_MANAGE"}]},
        ]
        expected = [
            {"group_name": reviewers, "permission_level": "CAN_USE"},
            {"service_principal_name": "ci", "permission_level": "CAN_USE"},
        ]
        applied = existing + [
            {"group_name": reviewers, "all_permissions": [{"permission_level": "CAN_USE"}]},
            {"service_principal_name": "ci", "all_permissions": [{"permission_level": "CAN_USE"}]},
        ]
        with patch.object(provision, "dbx", side_effect=[
            {"access_control_list": existing}, {}, {"access_control_list": applied},
        ]) as dbx:
            provision.configure_warehouse(args, apps, (reviewers,))
            provision.configure_warehouse(args, apps, (reviewers,))

        self.assertFalse(any(item.args[1:4] == ("permissions", "set", "warehouses")
                             for item in dbx.call_args_list))
        self.assertEqual(dbx.call_args_list, [
            call("synthetic", "permissions", "get", "warehouses", "dead"),
            call("synthetic", "permissions", "update", "warehouses", "dead",
                 payload={"access_control_list": expected}),
            call("synthetic", "permissions", "get", "warehouses", "dead"),
        ])

    def test_warehouse_adds_group_without_replacing_existing_privileges(self):
        args = Namespace(workspace_profile="synthetic", warehouse_id="dead", ron_user="operator@example.invalid")
        acl = {"access_control_list": [
            {"user_name": args.ron_user, "all_permissions": [{"permission_level": "IS_OWNER"}]},
        ]}
        with patch.object(provision, "dbx", side_effect=[acl, {}]) as dbx:
            provision.configure_warehouse(args, {"integration": "ci", "reader": "reader"}, ("reviewers", "identifier-readers"))
        self.assertEqual(dbx.call_args, call(
            "synthetic", "permissions", "update", "warehouses", "dead", payload={"access_control_list": [
                {"group_name": "identifier-readers", "permission_level": "CAN_USE"},
                {"group_name": "reviewers", "permission_level": "CAN_USE"},
                {"service_principal_name": "ci", "permission_level": "CAN_USE"},
                {"service_principal_name": "reader", "permission_level": "CAN_USE"},
            ]},
        ))

    def test_workspace_local_group_cannot_receive_catalog_grants(self):
        args = Namespace(workspace_profile="synthetic", environment="dev")
        with (
            patch.object(provision, "find", return_value={"id": "1"}),
            patch.object(provision, "dbx", return_value={"Resources": []}) as dbx,
            self.assertRaisesRegex(RuntimeError, "legacy workspace-local group ledgerlens-dev-reviewers"),
        ):
            provision.create_reviewer_group(args, {"id": "2"})
        self.assertEqual(dbx.call_count, 1)

    def test_reviewer_account_group_creation_and_existing_membership(self):
        args = Namespace(workspace_profile="synthetic", environment="demo")
        group_path = "/api/2.0/account/scim/v2/Groups"
        ron = {"id": 2}
        with (
            patch.object(provision, "find", return_value=None),
            patch.object(provision, "dbx", side_effect=[
                {"Resources": []}, {"id": "3"}, {"id": "3", "members": []}, None,
            ]) as dbx,
            patch.object(provision, "assign_sql_group") as assign,
        ):
            self.assertEqual(provision.create_reviewer_group(args, ron), "ledgerlens-demo-reviewers")
        self.assertEqual(dbx.call_args_list[1:3], [
            call("synthetic", "api", "post", group_path, payload={"displayName": "ledgerlens-demo-reviewers"}),
            call("synthetic", "api", "get", f"{group_path}/3"),
        ])
        self.assertEqual(dbx.call_args.args, ("synthetic", "api", "patch", f"{group_path}/3"))
        self.assertEqual(dbx.call_args.kwargs["payload"]["Operations"][0]["value"], [{"value": "2"}])
        assign.assert_called_once_with("synthetic", 3)

        with (
            patch.object(provision, "find", return_value={"id": "3"}),
            patch.object(provision, "dbx", side_effect=[
                {"Resources": [{"id": "3"}]}, {"id": "3", "members": [{"value": "2"}]},
            ]) as dbx,
            patch.object(provision, "assign_sql_group") as assign,
        ):
            self.assertEqual(provision.create_reviewer_group(args, ron), "ledgerlens-demo-reviewers")
        self.assertEqual(dbx.call_count, 2)
        assign.assert_called_once_with("synthetic", 3)

    def test_reviewer_workspace_assignment_is_created_or_reconciled_idempotently(self):
        path = "/api/2.0/identity/workspace-assignments"
        entitlements = ["DATABRICKS_SQL_ACCESS", "WORKSPACE_ACCESS"]
        with patch.object(provision, "dbx", side_effect=[{}, None]) as dbx:
            provision.assign_sql_group("synthetic", 3)
        self.assertEqual(dbx.call_args, call(
            "synthetic", "api", "post", path, payload={"principal_id": 3, "entitlements": entitlements},
        ))

        assignment = {"workspace_assignments": [{"principal_id": "3"}]}
        with patch.object(provision, "dbx", side_effect=[
            {"next_page_token": "next/page"}, assignment, {"entitlements": ["WORKSPACE_ACCESS"]}, None,
        ]) as dbx:
            provision.assign_sql_group("synthetic", 3)
        self.assertEqual(dbx.call_args_list[1], call("synthetic", "api", "get", f"{path}?page_token=next%2Fpage"))
        self.assertEqual(dbx.call_args, call(
            "synthetic", "api", "patch", f"{path}/3?update_mask=entitlements",
            payload={"principal_id": 3, "entitlements": entitlements},
        ))
        with patch.object(provision, "dbx", side_effect=[assignment, {"entitlements": entitlements}]) as dbx:
            provision.assign_sql_group("synthetic", 3)
        self.assertEqual(dbx.call_count, 2)
