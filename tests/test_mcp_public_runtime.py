import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.databricks import DatabricksError, ReaderConfig, RuntimeConfig

with patch.dict(sys.modules, {"streamlit": MagicMock()}):
    from ledgerlens.app.ledgerlens_ui import runtime_panel as panel


def answer_text(text):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


class PublicRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = panel.PublicRuntime()
        self.config = RuntimeConfig(ReaderConfig(
            "https://workspace.invalid", "ledgerlens_demo", "a" * 16, "demo-reader", "synthetic-secret",
        ), "databricks-gpt-oss-20b")
        self.run_id = "published-1"
        self.read = Mock(side_effect=self._read)
        self.chat = Mock(return_value=answer_text("Published wages trace to the W-2."))
        self.workspace = Mock(read=self.read, chat_gateway=self.chat)
        patcher = patch("ledgerlens.mcp.databricks.ReaderWorkspace", return_value=self.workspace)
        self.connect = patcher.start()
        self.addCleanup(patcher.stop)

    def _read(self, sql, params, *, row_limit):
        if "latest_publication" in sql:
            return [[self.run_id]]
        return [["a" * 64, "person", "2024", "W-2", params["output_field"], "53000.00",
                 "doc", "field", "attempt", None, "extractor", "1", None, None]]

    def test_one_model_request_per_run_and_preset_across_concurrent_sessions(self):
        barrier = Barrier(2)

        def ask():
            barrier.wait(timeout=5)
            return self.runtime.answer(self.config, panel.PRESETS[0])

        with ThreadPoolExecutor(max_workers=2) as callers:
            replies = list(callers.map(lambda _: ask(), range(2)))
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(replies[0].run_id, self.run_id)
        self.assertTrue(replies[0].answer.grounded)
        self.assertEqual(replies[0].answer.tool_calls[0].run_id, self.run_id)
        self.chat.assert_called_once()
        self.connect.assert_called_with(self.config.reader)
        endpoint, payload = self.chat.call_args.args
        self.assertEqual(endpoint, "databricks-gpt-oss-20b")
        self.assertNotIn("tools", payload)
        data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(data["trace_value"]["run_id"], self.run_id)
        self.assertEqual(data["trace_value"]["rows"][0]["doc_id"], "doc")
        sql, parameters = self.read.call_args.args
        self.assertIn("ledgerlens_demo.gold.published_lineage", sql)
        self.assertEqual(parameters["run_id"], self.run_id)

        self.runtime.answer(self.config, panel.PRESETS[1])
        self.run_id = "published-2"
        fresh = self.runtime.answer(self.config, panel.PRESETS[0])
        self.assertEqual(fresh.run_id, "published-2")
        self.assertEqual(self.chat.call_count, 3)

    def test_daily_model_cap_resets_without_replaying_completed_pairs(self):
        today = Mock(return_value=date(2026, 11, 25))
        runtime = panel.PublicRuntime(daily_limit=1, today=today)
        first = runtime.answer(self.config, panel.PRESETS[0])
        self.assertIsNone(runtime.answer(self.config, panel.PRESETS[1]).answer)
        self.assertEqual(runtime.answer(self.config, panel.PRESETS[0]), first)
        self.chat.assert_called_once()

        today.return_value = date(2026, 11, 26)
        self.assertEqual(runtime.answer(self.config, panel.PRESETS[0]), first)
        self.assertTrue(runtime.answer(self.config, panel.PRESETS[1]).answer.grounded)
        self.assertEqual(self.chat.call_count, 2)

    def test_model_permission_quota_and_capacity_errors_stop_calls_for_the_day(self):
        for status, code in ((403, "PERMISSION_DENIED"), (429, "RESOURCE_EXHAUSTED"), (503, None)):
            with self.subTest(status=status):
                self.chat.reset_mock()
                self.chat.side_effect = [DatabricksError("provider details", status=status, code=code),
                                         answer_text("Published other income.")]
                today = Mock(return_value=date(2026, 11, 25))
                runtime = panel.PublicRuntime(today=today)
                failed = runtime.answer(self.config, panel.PRESETS[0])
                self.assertFalse(failed.answer.grounded)
                self.assertNotIn("provider details", failed.answer.text)
                self.assertIsNone(runtime.answer(self.config, panel.PRESETS[1]).answer)
                self.chat.assert_called_once()
                today.return_value = date(2026, 11, 26)
                self.assertEqual(runtime.answer(self.config, panel.PRESETS[0]), failed)
                self.assertTrue(runtime.answer(self.config, panel.PRESETS[1]).answer.grounded)
                self.assertEqual(self.chat.call_count, 2)

    def test_unlisted_questions_and_other_catalogs_do_not_reach_reader(self):
        with self.assertRaises(ValueError):
            self.runtime.answer(self.config, panel.Preset("Free text", "wages"))
        dev = RuntimeConfig(ReaderConfig(
            "https://workspace.invalid", "ledgerlens_dev", "a" * 16, "dev-reader", "synthetic-secret",
        ), self.config.endpoint)
        with self.assertRaises(ValueError):
            self.runtime.answer(dev, panel.PRESETS[0])
        self.connect.assert_not_called()

    def test_public_panel_exposes_only_presets_and_shows_tool_and_pin_on_rerun(self):
        st = MagicMock()
        st.session_state = {}
        settings = {
            "DATABRICKS_HOST": "https://workspace.invalid", "LEDGERLENS_CATALOG": "ledgerlens_demo",
            "DATABRICKS_WAREHOUSE_ID": "a" * 16, "DATABRICKS_READER_CLIENT_ID": "demo-reader",
            "DATABRICKS_READER_CLIENT_SECRET": "synthetic-secret", "LEDGERLENS_MODEL_ENDPOINT": self.config.endpoint,
        }
        st.button.side_effect = [True, False, False, False]
        with patch.object(panel, "st", st), patch.object(panel, "_PUBLIC_RUNTIME", self.runtime):
            panel.show_runtime_panel(None, fixture=False, settings=settings, public=True)
            self.assertEqual([c.args[0] for c in st.button.call_args_list], [p.question for p in panel.PRESETS])
            st.text_input.assert_not_called()
            st.caption.assert_called_once_with("Publication run published-1")
            st.expander.assert_called_once_with("trace_value · Publication run published-1")

            st.button.side_effect = [False] * 4
            panel.show_runtime_panel(None, fixture=False, settings=settings, public=True)
            self.assertEqual(st.text.call_count, 2)
        self.chat.assert_called_once()

    def test_public_panel_renders_unavailable_when_daily_budget_is_used(self):
        st = MagicMock()
        st.session_state = {}
        st.button.side_effect = [True, False, False, False]
        settings = {
            "DATABRICKS_HOST": "https://workspace.invalid", "LEDGERLENS_CATALOG": "ledgerlens_demo",
            "DATABRICKS_WAREHOUSE_ID": "a" * 16, "DATABRICKS_READER_CLIENT_ID": "demo-reader",
            "DATABRICKS_READER_CLIENT_SECRET": "synthetic-secret", "LEDGERLENS_MODEL_ENDPOINT": self.config.endpoint,
        }
        with patch.object(panel, "st", st), patch.object(panel, "_PUBLIC_RUNTIME", panel.PublicRuntime(daily_limit=0)):
            panel.show_public_runtime(settings)
        st.info.assert_called_once_with(panel.UNAVAILABLE)
        self.chat.assert_not_called()
