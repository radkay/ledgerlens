"""The app entry point for the Runtime agent panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from ledgerlens.mcp.agent import Answer
    from ledgerlens.mcp.databricks import RuntimeConfig

DAILY_MODEL_CALL_LIMIT = 12
UNAVAILABLE = "The Runtime agent is temporarily unavailable."


@dataclass(frozen=True)
class Preset:
    question: str
    output_field: str


PRESETS = (
    Preset("Where did the published W-2 wages come from?", "wages"),
    Preset("What other income was published?", "other_income"),
    Preset("What mortgage interest was published?", "mortgage_interest"),
    Preset("Which expected tax forms are missing?", "status"),
)


@dataclass(frozen=True)
class PublicReply:
    run_id: str
    answer: Answer | None


def _utc_day():
    return datetime.now(UTC).date()


class PublicRuntime:
    """Share model attempts and their results across public sessions in one process."""

    def __init__(self, daily_limit=DAILY_MODEL_CALL_LIMIT, today=_utc_day):
        self._daily_limit = daily_limit
        self._today = today
        self._lock = Lock()
        self._answers = {}
        self._day = None
        self._calls = 0
        self._blocked = False

    def _reset_budget_for_today(self):
        today = self._today()
        if today != self._day:
            self._day = today
            self._calls = 0
            self._blocked = False

    def answer(self, config: RuntimeConfig, preset: Preset) -> PublicReply:
        from ledgerlens.mcp.agent import answer_preset
        from ledgerlens.mcp.databricks import DatabricksError, ReaderWorkspace
        from ledgerlens.mcp.gold import GoldTools

        if preset not in PRESETS or config.reader.catalog != "ledgerlens_demo":
            raise ValueError("Use a public preset with the demo reader.")
        workspace = ReaderWorkspace(config.reader)
        publication = GoldTools(config.reader.catalog, workspace.read).pin()
        key = (publication.run_id, preset.question)

        # Serialize misses so concurrent sessions cannot repeat a model request or exceed the cap.
        with self._lock:
            if key in self._answers:
                return self._answers[key]
            self._reset_budget_for_today()
            if self._blocked or self._calls >= self._daily_limit:
                return PublicReply(publication.run_id, None)

            def complete(payload):
                self._reset_budget_for_today()
                self._calls += 1
                # Reserve this pair before sending the request, even if its outcome is unknown.
                self._answers[key] = PublicReply(publication.run_id, None)
                try:
                    return workspace.chat_gateway(config.endpoint, payload)
                except DatabricksError as error:
                    code = error.code or ""
                    if (
                        error.status in {401, 403, 429, 503}
                        or "QUOTA" in code or "CAPACITY" in code
                        or code in {"PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "REQUEST_LIMIT_EXCEEDED"}
                    ):
                        self._blocked = True
                    raise

            answer = answer_preset(preset.question, publication, preset.output_field, complete)
            reply = PublicReply(publication.run_id, answer)
            if key in self._answers:
                self._answers[key] = reply
            return reply


_PUBLIC_RUNTIME = PublicRuntime()


def show_public_runtime(settings):
    from ledgerlens.mcp.databricks import RuntimeConfig

    st.header("Runtime agent")
    try:
        config = RuntimeConfig.from_settings(settings) if settings else None
    except ValueError:
        config = None
    if config is None:
        for preset in PRESETS:
            st.button(preset.question, disabled=True)
        st.info(UNAVAILABLE)
        return

    state_key = "ledgerlens_public_runtime_reply"
    scope = (config.reader.host, config.reader.catalog, config.reader.client_id, config.endpoint)
    for preset in PRESETS:
        if st.button(preset.question):
            st.session_state.pop(state_key, None)
            try:
                with st.spinner("Reading published Gold"):
                    reply = _PUBLIC_RUNTIME.answer(config, preset)
            except (OSError, LookupError, RuntimeError, ValueError, ExceptionGroup):
                st.info(UNAVAILABLE)
                return
            st.session_state[state_key] = (scope, reply)

    saved_scope, reply = st.session_state.get(state_key, (None, None))
    if saved_scope != scope:
        st.session_state.pop(state_key, None)
        return
    if reply.answer and reply.answer.grounded:
        st.text(reply.answer.text)
    else:
        st.info(UNAVAILABLE)
    st.caption(f"Publication run {reply.run_id}")
    if reply.answer:
        for call in reply.answer.tool_calls:
            outcome = f"Publication run {call.run_id}" if call.succeeded else "Tool request failed"
            with st.expander(f"{call.name} · {outcome}"):
                st.json(call.arguments)


def show_runtime_panel(reader, *, fixture, settings=None, public=False):
    if public:
        show_public_runtime(settings)
    elif fixture:
        show_fixture_runtime(reader)
    elif settings and settings.get("LEDGERLENS_MODEL_ENDPOINT"):
        try:
            from ledgerlens.mcp.databricks import RuntimeConfig
            from ledgerlens.mcp.panel import show_runtime_agent
        except ModuleNotFoundError as error:
            if error.name not in {"ledgerlens", "ledgerlens.mcp", "ledgerlens.mcp.panel", "mcp"}:
                raise
            st.header("Runtime agent")
            st.info("The Runtime agent package is pending.")
            return
        show_runtime_agent(RuntimeConfig.from_settings(settings))
    else:
        st.header("Runtime agent")
        st.info("The Runtime agent is unavailable on this deployment.")


def show_fixture_runtime(reader):
    st.header("Runtime agent")
    st.caption("Synthetic fixture")
    prompt = "Where did the published W-2 wages come from?"
    if st.button(prompt):
        publication = reader.latest_publication()
        value = next(item for item in reader.lineage_values(publication.run_id) if item.output_field == "wages")
        links = reader.lineage(publication.run_id, gold_row_id=value.gold_row_id, output_field=value.output_field)
        st.write(f"Published wages of {value.output_value} trace to {len(links)} source field.")
        st.caption(f"Publication {publication.publication_seq} · {publication.run_id}")
        with st.expander("Fixture tool call", expanded=True):
            st.text("FixtureReader.lineage(run_id, gold_row_id, output_field)")
            st.json({"run_id": publication.run_id, "gold_row_id": value.gold_row_id,
                     "output_field": value.output_field})
