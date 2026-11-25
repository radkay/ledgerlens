"""The app entry point for the Runtime agent panel."""

import streamlit as st


def show_runtime_panel(reader, *, fixture, settings=None):
    if fixture:
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
