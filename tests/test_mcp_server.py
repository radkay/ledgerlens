from datetime import timedelta
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.gold import GoldTools
from ledgerlens.mcp.server import create_server, published_session


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_stdio_entry_point_lists_the_two_tools(self):
        source = str(Path(__file__).resolve().parents[1] / "src")
        pythonpath = os.pathsep.join(filter(None, (source, os.environ.get("PYTHONPATH", ""))))
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ledgerlens.mcp.server"],
            env={
                "PYTHONPATH": pythonpath,
                "DATABRICKS_HOST": "https://workspace.invalid",
                "LEDGERLENS_CATALOG": "ledgerlens_dev",
                "DATABRICKS_WAREHOUSE_ID": "a" * 16,
                "DATABRICKS_READER_CLIENT_ID": "synthetic-reader",
                "DATABRICKS_READER_CLIENT_SECRET": "synthetic-secret",
            },
            encoding="utf-8",
            encoding_error_handler="strict",
        )
        async with stdio_client(server) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=timedelta(seconds=15)) as session:
                self.assertEqual((await session.initialize()).serverInfo.name, "ledgerlens")
                self.assertEqual({tool.name for tool in (await session.list_tools()).tools},
                                 {"trace_value", "documents_needing_review"})

    async def test_rejects_arbitrary_queries_objects_and_invalid_arguments_before_reads(self):
        read = Mock()
        server = create_server(GoldTools("ledgerlens_dev", read).pin)
        async with create_connected_server_and_client_session(server) as session:
            self.assertEqual(
                [tool.name for tool in (await session.list_tools()).tools],
                ["trace_value", "documents_needing_review"],
            )
            for name, args in (
                ("sql", {"query": "SELECT 1"}),
                ("trace_value", {"catalog": "ledgerlens_demo"}),
                ("trace_value", {"gold_row_id": 1}),
                ("trace_value", {"gold_row_id": "a" * 64 + "\n"}),
                ("trace_value", {"output_field": "ledgerlens_dev.silver.fields"}),
                ("documents_needing_review", {"row_limit": 1000}),
            ):
                with self.subTest(name=name, args=args):
                    self.assertTrue((await session.call_tool(name, args)).isError)
            read.assert_not_called()

    async def test_calls_use_one_pinned_publication_and_return_structured_evidence(self):
        read = Mock(side_effect=[[["run"]], [], [["3"]], []])
        gold = GoldTools("ledgerlens_dev", read).pin()
        async with published_session(gold) as session:
            trace = await session.call_tool("trace_value", {"output_field": "wages"})
            review = await session.call_tool("documents_needing_review", {})
        self.assertFalse(trace.isError)
        self.assertEqual(trace.structuredContent, {"run_id": "run", "rows": [], "truncated": False})
        self.assertFalse(review.isError)
        self.assertEqual(review.structuredContent["run_id"], "run")
        self.assertEqual(review.structuredContent["published_document_count"], 3)
        self.assertEqual(read.call_count, 4)
