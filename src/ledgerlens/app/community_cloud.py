"""Public-only entry point for Streamlit Community Cloud."""

from pathlib import Path
import sys

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

app_directory = str(Path(__file__).resolve().parent)
if app_directory not in sys.path:
    sys.path.insert(0, app_directory)

from streamlit_app import main

try:
    settings = st.secrets.to_dict()
except StreamlitSecretNotFoundError:
    settings = {}

main(settings, public_only=True)
