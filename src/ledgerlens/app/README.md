# LedgerLens Streamlit app

The app starts with its bundled synthetic publication. The overview separates current document state from snapshot-based confidence, review volume and extraction accuracy. Published outputs includes forward and reverse lineage and PDF and JSON downloads of the selected pre-filled form.

## Run locally

From the repository root, use Python 3.12 and an isolated virtual environment.

```sh
python -m pip install -r src/ledgerlens/app/requirements.txt
streamlit run src/ledgerlens/app/streamlit_app.py
```

## Streamlit Community Cloud

Select `src/ledgerlens/app/streamlit_app.py` as the entry point and Python 3.12 in advanced settings. Community Cloud installs the adjacent requirements file. Fixture mode needs no secrets. The Cloud app runs in public read-only mode. [Community Cloud dependency placement](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)

## Databricks Apps

Deploy the contents of `src/ledgerlens/app` together as the source directory. The included `app.yaml` starts Streamlit. The host provides the process address and port.

To enable the authenticated fixture workbench in Databricks Apps, configure app user authorization and set `LEDGERLENS_ENVIRONMENT` to `dev` or `demo` and `LEDGERLENS_OPERATOR_USER_ID` to the Databricks user ID for Ron in that workspace. The app checks the forwarded user token against the workspace's current-user endpoint and the matching reviewer group. Fixture decisions and batch events are session-local. [Databricks Apps user authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
