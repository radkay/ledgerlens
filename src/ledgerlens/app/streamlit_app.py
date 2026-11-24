from pathlib import Path
import sys

import streamlit as st

# Support both repository entry points and a standalone Databricks Apps source directory.
app_directory = str(Path(__file__).resolve().parent)
if app_directory not in sys.path:
    sys.path.insert(0, app_directory)

from ledgerlens_ui.fixtures import FixtureReader
from ledgerlens_ui.overview import show_batches, show_overview

st.set_page_config(page_title="LedgerLens", layout="wide")
st.title("LedgerLens")
st.caption("Synthetic fixture · public mode")
show_overview(FixtureReader())
show_batches()
