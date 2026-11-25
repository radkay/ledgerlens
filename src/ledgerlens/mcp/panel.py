"""Streamlit entry point for an explicitly enabled live Runtime agent panel."""

import asyncio

from .agent import QUESTION_LIMIT, ask
from .databricks import RuntimeConfig


def show_runtime_agent(config: RuntimeConfig) -> None:
    import streamlit as st

    st.subheader("Ask about published Gold")
    state_key = "ledgerlens_runtime_agent_answer"
    scope = (config.reader.host, config.reader.catalog, config.reader.client_id, config.endpoint)
    with st.form("ledgerlens_runtime_agent"):
        question = st.text_input("Question", max_chars=QUESTION_LIMIT)
        submitted = st.form_submit_button("Ask")
    if submitted:
        st.session_state.pop(state_key, None)
        try:
            with st.spinner("Querying published Gold"):
                answer = asyncio.run(ask(config, question))
        except ValueError as error:
            st.warning(str(error))
            return
        except (OSError, LookupError, RuntimeError, ExceptionGroup):
            st.warning("Published Gold is unavailable.")
            return
        st.session_state[state_key] = (scope, answer)

    saved_scope, answer = st.session_state.get(state_key, (None, None))
    if saved_scope != scope:
        st.session_state.pop(state_key, None)
        return
    if answer.grounded:
        st.text(answer.text)
    else:
        st.warning(answer.text)
    for call in answer.tool_calls:
        publication = f"Publication run {call.run_id}" if call.run_id else "Tool request failed"
        with st.expander(f"{call.name} · {publication}"):
            st.json(call.arguments)
            if call.succeeded and call.name == "documents_needing_review":
                st.caption("The run ID applies to the published review count. Document statuses are live.")
