from functools import lru_cache
from os import environ
from pathlib import Path
import sys

import streamlit as st

# Support both repository entry points and a standalone Databricks Apps source directory.
app_directory = str(Path(__file__).resolve().parent)
if app_directory not in sys.path:
    sys.path.insert(0, app_directory)

from ledgerlens_ui.databricks import Workspace, authenticate_operator
from ledgerlens_ui.fixtures import FixtureBatch, FixtureReader, FixtureWorkbench
from ledgerlens_ui.live import CATALOGS, GoldReader, LiveBatch, LiveWorkbench, Sql
from ledgerlens_ui.overview import show_batches, show_overview
from ledgerlens_ui.views import show_outputs, show_workbench


def delegated_identity(workspace, environment):
    """Trust user delegation only on an explicitly configured Databricks Apps surface."""
    if not environ.get("DATABRICKS_APP_NAME") or workspace is None:
        return None, None
    actor_id = environ.get("LEDGERLENS_OPERATOR_USER_ID")
    token = st.context.headers.get("x-forwarded-access-token")
    operator = authenticate_operator(workspace, environment, actor_id, token)
    return (operator, token) if operator else (None, None)


def read_credentials():
    names = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_WAREHOUSE_ID")
    values = tuple(environ.get(name) for name in names)
    if not all(values):
        raise ValueError("Configure the reader credentials and explicit SQL warehouse.")
    return values


def clear_operator_state():
    for key in (
        "fixture_workbench", "fixture_batch", "operator_scope", "batch_job_run_id",
        "batch_poll_until", "batch_request_id", "pending_decision", "decision_saved",
    ):
        st.session_state.pop(key, None)


def diagnostics(workspace):
    if workspace and workspace.trace:
        with st.expander("Databricks calls"):
            for call, statement in workspace.trace:
                if statement:
                    st.code(statement, language="sql")
                else:
                    st.text(call)


def render():
    environment = environ.get("LEDGERLENS_ENVIRONMENT")
    source = environ.get("LEDGERLENS_DATA_SOURCE", "fixture")
    host = environ.get("DATABRICKS_HOST", "").rstrip("/")
    if source not in {"live", "fixture"}:
        raise ValueError("Choose fixture or live as the data source.")
    workspace = Workspace(host) if host and (source == "live" or environ.get("DATABRICKS_APP_NAME")) else None
    identity, user_token = delegated_identity(workspace, environment)
    scope = (source, environment, host, identity.actor_id) if identity else None
    if not scope or st.session_state.get("operator_scope") != scope:
        clear_operator_state()
    if scope:
        st.session_state["operator_scope"] = scope

    batch = workbench = None
    if source == "live":
        if workspace is None or environment not in CATALOGS:
            raise ValueError("Configure the live workspace and environment.")
        if not environ.get("DATABRICKS_APP_NAME") and environment != "demo":
            raise ValueError("Streamlit Community Cloud uses the demo environment.")
        client_id, client_secret, warehouse = read_credentials()

        @lru_cache(maxsize=1)
        def reader_token():
            return workspace.reader_token(client_id, client_secret)

        reader = GoldReader(Sql(workspace, warehouse, reader_token), environment)
        if identity:
            delegated = Sql(workspace, warehouse, lambda: user_token)
            workbench = LiveWorkbench(delegated, environment, identity.actor_id)
            job_id = environ.get("LEDGERLENS_PROCESSING_JOB_ID")
            if job_id and job_id.isascii() and job_id.isdecimal() and int(job_id) > 0:
                batch = LiveBatch(delegated, environment, int(job_id))
    else:
        reader = FixtureReader()
        if identity:
            if "fixture_workbench" not in st.session_state:
                st.session_state["fixture_workbench"] = FixtureWorkbench(identity.actor_id)
                st.session_state["fixture_batch"] = FixtureBatch()
            batch = st.session_state["fixture_batch"]
            workbench = st.session_state["fixture_workbench"]

    data_label = "Published Gold" if source == "live" else "Synthetic fixture"
    st.caption(f"{data_label} · {'Operator mode' if identity else 'Public mode'}")
    pages = ["Overview", "Published outputs"]
    if workbench:
        pages.append("Review workbench")
    page = st.sidebar.radio("Page", pages)
    if page == "Overview":
        show_overview(reader)
        show_batches(batch, authorize=lambda: delegated_identity(workspace, environment)[0] == identity)
    elif page == "Published outputs":
        show_outputs(reader)
    else:
        show_workbench(workbench)
    if source == "live":
        diagnostics(workspace)


st.set_page_config(page_title="LedgerLens", layout="wide", menu_items={})
st.title("LedgerLens")
try:
    render()
except PermissionError:
    st.error("Databricks denied access to the requested data.")
except OSError:
    st.error("Databricks data is temporarily unavailable. Refresh to try again.")
except ValueError:
    st.error("Check the app deployment configuration.")
