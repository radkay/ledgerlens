"""Recorded synthetic chat exchange replayed against a real in-process MCP server."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.agent import TOOL_CALL_LIMIT, answer_with_tools
from ledgerlens.mcp.gold import GoldTools
from ledgerlens.mcp.server import published_session


def assistant(*, text=None, calls=None):
    return {"choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": {
        "role": "assistant", "content": text, "tool_calls": calls,
    }}]}


def model_call(name, arguments, identifier="call_1"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}}


class RuntimeAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_replays_tool_use_and_grounds_final_answer_in_one_gold_run(self):
        wage = ["a" * 64, "person", "2024", None, "wages", "13.00", "doc", "field",
                "attempt", None, "extractor", "1", None, None]
        read = Mock(side_effect=[[["run-1"]], [wage], [["1"]], []])
        responses = [
            assistant(calls=[model_call("trace_value", '{"output_field":"wages"}')]),
            assistant(calls=[model_call("documents_needing_review", "{}", "call_2")]),
            assistant(text="Published wages were 13.00 and one document needed review in run-1."),
        ]
        complete = Mock(side_effect=responses)
        async with published_session(GoldTools("ledgerlens_dev", read).pin()) as session:
            answer = await answer_with_tools("What wages were published and how many needed review?", session,
                                             complete, "run-1")

        self.assertTrue(answer.grounded)
        self.assertEqual(answer.text, responses[2]["choices"][0]["message"]["content"])
        self.assertEqual([(call.name, call.run_id, call.succeeded) for call in answer.tool_calls], [
            ("trace_value", "run-1", True), ("documents_needing_review", "run-1", True),
        ])
        requests = [call.args[0] for call in complete.call_args_list]
        self.assertEqual([request["tool_choice"] for request in requests], ["required", "auto", "auto"])
        self.assertEqual([tool["function"]["name"] for tool in requests[0]["tools"]], [
            "trace_value", "documents_needing_review",
        ])
        tool_messages = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
        self.assertEqual([message["tool_call_id"] for message in tool_messages], ["call_1", "call_2"])
        self.assertEqual(json.loads(tool_messages[0]["content"])["rows"][0]["output_value"], "13.00")
        self.assertEqual(json.loads(tool_messages[1]["content"])["published_document_count"], 1)
        self.assertEqual(read.call_count, 4)

    async def test_rejects_ungrounded_content_and_unknown_tools(self):
        for first_response in (
            assistant(text="Invented result."),
            assistant(calls=[model_call("sql", '{"query":"SELECT * FROM ledgerlens_dev.eval.answer_key"}')]),
            assistant(calls=[model_call("trace_value", "{}")] * (TOOL_CALL_LIMIT + 1)),
        ):
            read = Mock(return_value=[["run-1"]])
            complete = Mock(return_value=first_response)
            async with published_session(GoldTools("ledgerlens_dev", read).pin()) as session:
                answer = await answer_with_tools("What was published?", session, complete, "run-1")
            self.assertFalse(answer.grounded)
            self.assertEqual(answer.tool_calls, ())
            read.assert_called_once()

    async def test_invalid_tool_arguments_do_not_ground_the_answer(self):
        read = Mock(return_value=[["run-1"]])
        complete = Mock(side_effect=[
            assistant(calls=[model_call("trace_value", '{"output_field":"bronze.documents"}')]),
            assistant(text="Invented result."),
        ])
        async with published_session(GoldTools("ledgerlens_dev", read).pin()) as session:
            answer = await answer_with_tools("What was published?", session, complete, "run-1")
        self.assertFalse(answer.grounded)
        self.assertFalse(answer.tool_calls[0].succeeded)
        self.assertIsNone(answer.tool_calls[0].run_id)
        read.assert_called_once()
