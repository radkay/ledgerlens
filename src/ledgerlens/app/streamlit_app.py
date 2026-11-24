import streamlit as st

from ledgerlens_ui.fixtures import FixtureReader
from ledgerlens_ui.overview import show_batches, show_overview

st.set_page_config(page_title="LedgerLens", layout="wide")
st.title("LedgerLens")
st.caption("Synthetic fixture · public mode")
show_overview(FixtureReader())
show_batches()
