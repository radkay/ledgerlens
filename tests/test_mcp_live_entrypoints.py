import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.databricks import ReaderConfig, RuntimeConfig
from ledgerlens.mcp.smoke import verify_runtime

from tests.test_mcp_agent import assistant, model_call

MANAGED_SCRIPT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "managed_dbsql_mcp.py"
spec = importlib.util.spec_from_file_location("managed_dbsql_mcp", MANAGED_SCRIPT)
managed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(managed)


class DevSmokeTests(unittest.IsolatedAsyncioTestCase):
    @patch("ledgerlens.mcp.smoke.ReaderWorkspace")
    async def test_checks_gold_lineage_and_model_answer_for_same_run(self, workspace):
        config = RuntimeConfig(
            ReaderConfig("https://workspace.example", "ledgerlens_dev", "a" * 16, "reader", "secret"),
            "databricks-gpt-oss-20b",
        )
        row_id = "a" * 64
        wage = [row_id, "person", "2024", None, "wages", "53000.01", "doc", "field",
                "attempt", None, "extractor", "1", None, None]
        workspace.return_value.read.side_effect = [[["run"]], [wage], [wage], [wage]]
        workspace.return_value.chat.side_effect = [
            assistant(calls=[model_call("trace_value", json.dumps({
                "gold_row_id": row_id, "output_field": "wages",
            }))]),
            assistant(text="The published wage was $53,000.01 in run."),
        ]
        with patch("builtins.print"):
            result = await verify_runtime(config, expected_run_id="run")
        self.assertEqual((result.run_id, result.gold_query_rows, result.lineage_rows), ("run", 1, 1))
        self.assertEqual(result.agent_tools, ("trace_value",))
        self.assertEqual(workspace.return_value.read.call_count, 4)


def managed_result(payload):
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))])


class ManagedProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        printer = patch.object(managed, "print", create=True)
        self.print = printer.start()
        self.addCleanup(printer.stop)

    def session(self, *payloads):
        session = Mock()
        session.list_tools = AsyncMock(return_value=SimpleNamespace(
            tools=[SimpleNamespace(name="execute_sql_read_only"), SimpleNamespace(name="poll_sql_result")],
            nextCursor=None,
        ))
        session.call_tool = AsyncMock(side_effect=[managed_result(payload) for payload in payloads])
        return session

    async def test_uses_fixed_read_only_query_and_polls_same_statement(self):
        session = self.session(
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {"status": {"state": "SUCCEEDED"}, "result": {"data_typed_array": [["run-1"]]}},
        )
        with patch.object(managed.asyncio, "sleep", new_callable=AsyncMock):
            result = await managed.query_publication(session, "a" * 16, "run-1")
        self.assertEqual(result, {
            "managed_tool": "execute_sql_read_only", "run_id": "run-1",
            "statement_id": "statement-1", "polls": 1, "row_count": 1,
        })
        execute, poll = session.call_tool.await_args_list
        self.assertEqual(execute.args[0], "execute_sql_read_only")
        self.assertEqual(execute.kwargs["meta"], {"warehouse_id": "a" * 16})
        query = execute.args[1]["query"]
        self.assertEqual(query, managed.QUERY)
        self.assertIn("ledgerlens_dev.gold.latest_publication", query)
        self.assertIn("l.run_id = p.run_id", query)
        self.assertTrue(query.strip().endswith("LIMIT 1"))
        self.assertEqual(poll.args, ("poll_sql_result", {"statement_id": "statement-1"}))

    async def test_uses_documented_execute_sql_from_paginated_tool_list(self):
        session = self.session(
            {"statement_id": "statement-1", "status": {"state": "SUCCEEDED"},
             "result": {"data_typed_array": [["run-1"]]}},
        )
        session.list_tools.side_effect = [
            SimpleNamespace(tools=[SimpleNamespace(name="execute_sql", description="private description")],
                            nextCursor="private cursor"),
            SimpleNamespace(tools=[SimpleNamespace(name="poll_sql_result")], nextCursor=None),
        ]
        result = await managed.query_publication(session, "a" * 16, "run-1")
        self.assertEqual(result["managed_tool"], "execute_sql")
        session.call_tool.assert_awaited_once_with(
            "execute_sql", {"query": managed.QUERY}, meta={"warehouse_id": "a" * 16},
        )
        self.assertEqual(session.list_tools.await_args.kwargs["params"].cursor, "private cursor")
        self.assertEqual(json.loads(self.print.call_args.args[0]), {
            "managed_tools": ["execute_sql", "poll_sql_result"], "redacted_tool_names": 0,
        })
        self.assertTrue(self.print.call_args.kwargs["flush"])

    async def test_logs_available_names_and_fails_closed_on_mismatch(self):
        session = self.session()
        session.list_tools.return_value = SimpleNamespace(
            tools=[SimpleNamespace(name="other_tool"), SimpleNamespace(name="invalid secret text")],
            nextCursor=None,
        )
        with self.assertRaisesRegex(managed.ProbeError, "did not list poll_sql_result"):
            await managed.query_publication(session, "a" * 16, "run-1")
        self.assertEqual(json.loads(self.print.call_args.args[0]), {
            "managed_tools": ["other_tool"], "redacted_tool_names": 1,
        })
        session.call_tool.assert_not_awaited()

    async def test_rejects_other_publication_and_stops_polling(self):
        session = self.session({
            "statement_id": "statement-1", "status": {"state": "SUCCEEDED"},
            "result": {"data_typed_array": [["previous-run"]]},
        })
        with self.assertRaisesRegex(RuntimeError, "different publication"):
            await managed.query_publication(session, "a" * 16, "expected-run")

        pending = {"statement_id": "statement-1", "status": {"state": "RUNNING"}}
        session = self.session(*([pending] * (managed.POLL_LIMIT + 1)))
        with patch.object(managed.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(TimeoutError, "polling limit"):
                await managed.query_publication(session, "a" * 16, None)
        self.assertEqual(session.call_tool.await_count, managed.POLL_LIMIT + 1)
