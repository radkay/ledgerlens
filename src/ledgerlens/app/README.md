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

## Live data

Keep the fixture source until a verified Gold publication exists. Set `LEDGERLENS_DATA_SOURCE=live`, explicitly select `LEDGERLENS_ENVIRONMENT` and supply the existing warehouse ID through `DATABRICKS_WAREHOUSE_ID`. No other environment is selected on failure. The dashboard shows accuracy as pending when the evaluator has yet to write rows for the pinned run. Live confidence distribution shows pending until the publication-keyed Gold aggregate is available. The synthetic fixture source continues to display its own confidence chart.

For Community Cloud, use `demo` and set `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` as root-level secrets for the demo reader. Databricks Apps provides the workspace URL and the managed app principal's OAuth credentials. That principal needs the contract's Gold reader grants on the selected catalog and explicit warehouse. [Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management) · [Databricks Apps runtime environment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env)

For live Databricks Apps operator actions, enable the user authorization scopes for current-user identity, SQL and jobs. Configure the operator ID as for fixture mode and set `LEDGERLENS_PROCESSING_JOB_ID` to the matching deployed Processing job. The app checks the resolved job name and single-run configuration. Batch status comes from `ops.pipeline_runs`, with Jobs API terminal status covering shutdowns before the pipeline records a terminal event. The live workbench serves the vetted masked bootstrap W-2 preview for its exact document hash. Other document hashes show preview unavailable. Generated masked previews for additional documents are a Processing lane follow-up. [Databricks user authorization scopes](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) · [Jobs run-now idempotency](https://docs.databricks.com/api/jobs/v2/run-now)
