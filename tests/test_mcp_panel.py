import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledgerlens.mcp.agent import Answer, ToolCall
from ledgerlens.mcp.databricks import ReaderConfig, RuntimeConfig
from ledgerlens.mcp.panel import show_runtime_agent


class RuntimePanelTests(unittest.TestCase):
    def test_offers_question_and_displays_evidence_from_actual_call_results(self):
        config = RuntimeConfig(
            ReaderConfig("https://workspace.example", "ledgerlens_dev", "a" * 16, "reader", "secret"),
            "databricks-gpt-oss-20b",
        )
        st = MagicMock()
        st.session_state = {}
        st.text_input.return_value = "What was published?"
        st.form_submit_button.return_value = True
        published = Answer("Run 1234 published wages.", (
            ToolCall("trace_value", {"output_field": "wages"}, "1234", True),
            ToolCall("documents_needing_review", {}, "1234", True),
        ), True)
        with (
            patch.dict(sys.modules, {"streamlit": st}),
            patch("ledgerlens.mcp.panel.ask", new=AsyncMock(return_value=published)) as ask,
        ):
            show_runtime_agent(config)
        ask.assert_awaited_once_with(config, "What was published?")
        st.text.assert_called_once_with(published.text)
        self.assertEqual([call.args[0] for call in st.expander.call_args_list], [
            "trace_value · Publication run 1234", "documents_needing_review · Publication run 1234",
        ])
        st.caption.assert_called_once()
