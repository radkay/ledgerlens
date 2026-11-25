import unittest
from argparse import Namespace
from unittest.mock import call, patch

from provisioning import provision


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
                f"JOIN {catalog}.ops.publications AS publication ON snapshot.run_id = publication.run_id"
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
        warehouse.assert_called_once_with(args, dict(zip(
            ("processor", "evaluator", "integration", "reader"), principal_ids, strict=True,
        )), group)

        outputs = (
            "bronze.documents", "bronze.extractions", "silver.fields", "gold.person_year",
            "gold.missing_documents", "gold.prefill", "gold.lineage", "gold.review_summary",
            "gold.confidence_summary", "ops.pipeline_runs", "ops.publications", "ops.document_releases",
        )
        serving = (
            "gold.latest_publication", "gold.current_person_year", "gold.current_missing_documents",
            "gold.current_prefill", "gold.current_lineage", "gold.current_review_summary",
            "gold.current_confidence_summary", "gold.current_extraction_accuracy",
            "gold.current_document_state",
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
            provision.configure_warehouse(args, apps, reviewers)
            provision.configure_warehouse(args, apps, reviewers)

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
            provision.configure_warehouse(args, {"integration": "ci", "reader": "reader"}, "reviewers")
        self.assertEqual(dbx.call_args, call(
            "synthetic", "permissions", "update", "warehouses", "dead", payload={"access_control_list": [
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
            patch.object(provision, "assign_reviewer_group") as assign,
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
            patch.object(provision, "assign_reviewer_group") as assign,
        ):
            self.assertEqual(provision.create_reviewer_group(args, ron), "ledgerlens-demo-reviewers")
        self.assertEqual(dbx.call_count, 2)
        assign.assert_called_once_with("synthetic", 3)

    def test_reviewer_workspace_assignment_is_created_or_reconciled_idempotently(self):
        path = "/api/2.0/identity/workspace-assignments"
        entitlements = ["DATABRICKS_SQL_ACCESS", "WORKSPACE_ACCESS"]
        with patch.object(provision, "dbx", side_effect=[{}, None]) as dbx:
            provision.assign_reviewer_group("synthetic", 3)
        self.assertEqual(dbx.call_args, call(
            "synthetic", "api", "post", path, payload={"principal_id": 3, "entitlements": entitlements},
        ))

        assignment = {"workspace_assignments": [{"principal_id": "3"}]}
        with patch.object(provision, "dbx", side_effect=[
            {"next_page_token": "next/page"}, assignment, {"entitlements": ["WORKSPACE_ACCESS"]}, None,
        ]) as dbx:
            provision.assign_reviewer_group("synthetic", 3)
        self.assertEqual(dbx.call_args_list[1], call("synthetic", "api", "get", f"{path}?page_token=next%2Fpage"))
        self.assertEqual(dbx.call_args, call(
            "synthetic", "api", "patch", f"{path}/3?update_mask=entitlements",
            payload={"principal_id": 3, "entitlements": entitlements},
        ))
        with patch.object(provision, "dbx", side_effect=[assignment, {"entitlements": entitlements}]) as dbx:
            provision.assign_reviewer_group("synthetic", 3)
        self.assertEqual(dbx.call_count, 2)
