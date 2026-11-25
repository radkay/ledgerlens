"""Public-only entry point for Streamlit Community Cloud."""

from pathlib import Path
import sys

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

app_directory = Path(__file__).resolve().parent
for path in (app_directory.parent.parent, app_directory):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from streamlit_app import main

try:
    settings = st.secrets.to_dict()
except StreamlitSecretNotFoundError:
    settings = {}

main(settings, public_only=True)
