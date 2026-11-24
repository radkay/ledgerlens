from os import environ
from pathlib import Path
import sys

import streamlit as st

# Support both repository entry points and a standalone Databricks Apps source directory.
app_directory = str(Path(__file__).resolve().parent)
if app_directory not in sys.path:
    sys.path.insert(0, app_directory)

from ledgerlens_ui.databricks import REVIEWER_GROUPS, Workspace, authenticate_operator
from ledgerlens_ui.fixtures import FixtureBatch, FixtureReader, FixtureWorkbench
from ledgerlens_ui.overview import show_batches, show_overview
from ledgerlens_ui.views import show_outputs, show_workbench


def fixture_operator():
    """Trust a delegated token only on the explicitly enabled Databricks Apps surface."""
    environment = environ.get("LEDGERLENS_ENVIRONMENT")
    if not environ.get("DATABRICKS_APP_NAME") or environment not in REVIEWER_GROUPS:
        return None
    host = environ.get("DATABRICKS_HOST")
    actor_id = environ.get("LEDGERLENS_OPERATOR_USER_ID")
    token = st.context.headers.get("x-forwarded-access-token")
    if not host or not actor_id or not token:
        return None
    try:
        return authenticate_operator(Workspace(host.rstrip("/")), environment, actor_id, token)
    except ValueError:
        return None


def clear_operator_state():
    for key in (
        "fixture_workbench", "fixture_batch", "operator_actor", "batch_job_run_id",
        "batch_poll_until", "batch_request_id", "pending_decision", "decision_saved",
    ):
        st.session_state.pop(key, None)


def main():
    st.set_page_config(page_title="LedgerLens", layout="wide", menu_items={})
    st.title("LedgerLens")
    reader = FixtureReader()
    operator = fixture_operator()
    if operator is None or st.session_state.get("operator_actor") != operator.actor_id:
        clear_operator_state()
    if operator:
        st.session_state["operator_actor"] = operator.actor_id
        if "fixture_workbench" not in st.session_state:
            st.session_state["fixture_workbench"] = FixtureWorkbench(operator.actor_id)
            st.session_state["fixture_batch"] = FixtureBatch()

    st.caption(f"Synthetic fixture · {'Operator mode' if operator else 'Public mode'}")
    pages = ["Overview", "Published outputs"]
    if operator:
        pages.append("Review workbench")
    page = st.sidebar.radio("Page", pages)
    if page == "Overview":
        show_overview(reader)
        # Fragments run independently of main, so recheck the delegated identity before use.
        batch = st.session_state.get("fixture_batch") if operator else None
        show_batches(batch, authorize=lambda: fixture_operator() == operator)
    elif page == "Published outputs":
        show_outputs(reader)
    else:
        show_workbench(st.session_state["fixture_workbench"])


main()
