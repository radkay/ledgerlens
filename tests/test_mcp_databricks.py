import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.databricks import DatabricksError, ReaderConfig, ReaderWorkspace, RuntimeConfig


class ReaderTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "DATABRICKS_HOST": "https://workspace.example/",
            "LEDGERLENS_CATALOG": "ledgerlens_dev",
            "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef",
            "DATABRICKS_READER_CLIENT_ID": "reader",
            "DATABRICKS_READER_CLIENT_SECRET": "synthetic-reader-secret",
            "DATABRICKS_CLIENT_ID": "ci",
            "DATABRICKS_CLIENT_SECRET": "synthetic-ci-secret",
            "LEDGERLENS_MODEL_ENDPOINT": "databricks-gpt-oss-20b",
        }
        self.config = ReaderConfig.from_settings(self.settings)
        self.workspace = ReaderWorkspace(self.config)

    def test_requires_explicit_reader_catalog_warehouse_and_model(self):
        for key in (
            "LEDGERLENS_CATALOG", "LEDGERLENS_MODEL_ENDPOINT", "DATABRICKS_WAREHOUSE_ID",
            "DATABRICKS_READER_CLIENT_ID", "DATABRICKS_READER_CLIENT_SECRET", "DATABRICKS_HOST",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                RuntimeConfig.from_settings({**self.settings, key: ""})
        for host in ("http://workspace.example", "https://user@workspace.example", "https://workspace.example/a"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                ReaderConfig.from_settings({**self.settings, "DATABRICKS_HOST": host})
        self.assertNotIn(self.settings["DATABRICKS_READER_CLIENT_SECRET"], repr(self.config))
        with self.assertRaises(ValueError):
            self.workspace.chat("other-endpoint", {})

    @patch("ledgerlens.mcp.databricks._post")
    def test_authenticates_reader_and_bounds_sql_and_model_requests(self, post):
        post.side_effect = [
            {"access_token": "synthetic-token", "expires_in": 3600},
            {"status": {"state": "SUCCEEDED"}, "result": {"data_array": [["published"]]}},
            {"choices": []},
        ]
        reader_sql = "SELECT run_id FROM ledgerlens_dev.gold.latest_publication LIMIT 1"
        self.assertEqual(self.workspace.read(reader_sql, {}, row_limit=1), [["published"]])
        self.workspace.chat("databricks-gpt-oss-20b", {"messages": []})

        auth, read, chat = post.call_args_list
        self.assertEqual(auth.args[0], "https://workspace.example/oidc/v1/token")
        self.assertEqual(auth.args[1]["Authorization"], "Basic cmVhZGVyOnN5bnRoZXRpYy1yZWFkZXItc2VjcmV0")
        self.assertEqual(auth.args[2], b"grant_type=client_credentials&scope=all-apis")
        self.assertEqual(read.args[0], "https://workspace.example/api/2.0/sql/statements")
        self.assertEqual(read.args[1]["Authorization"], "Bearer synthetic-token")
        self.assertEqual(json.loads(read.args[2]), {
            "warehouse_id": "0123456789abcdef", "statement": reader_sql, "parameters": [],
            "wait_timeout": "10s", "on_wait_timeout": "CANCEL", "disposition": "INLINE",
            "format": "JSON_ARRAY", "row_limit": 1, "byte_limit": 131_072,
        })
        self.assertEqual(chat.args[0], "https://workspace.example/serving-endpoints/databricks-gpt-oss-20b/invocations")
        self.assertEqual(chat.args[1]["Authorization"], "Bearer synthetic-token")

    @patch.object(ReaderWorkspace, "_request")
    def test_rejects_failed_and_over_bound_statements(self, request):
        sql = "SELECT run_id FROM ledgerlens_dev.gold.latest_publication LIMIT 1"
        for result in (
            {"status": {"state": "FAILED"}},
            {"status": {"state": "SUCCEEDED"}, "manifest": {"truncated": True}},
            {"status": {"state": "SUCCEEDED"}, "result": {"next_chunk_index": 1}},
            {"status": {"state": "SUCCEEDED"}, "result": {"data_array": [["one"], ["two"]]}},
        ):
            with self.subTest(result=result), self.assertRaises(DatabricksError):
                request.return_value = result
                self.workspace.read(sql, {}, row_limit=1)
        with self.assertRaises(ValueError):
            self.workspace.read(sql, {}, row_limit=200)
