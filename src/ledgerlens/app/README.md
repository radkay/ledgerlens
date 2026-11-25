# LedgerLens Streamlit app

The app starts with its bundled synthetic publication. The overview separates current document state from snapshot-based confidence, review volume and extraction accuracy. Published outputs includes expected-document coverage, released person-year totals, forward and reverse lineage, and PDF and JSON downloads of the selected pre-filled form.

## Run locally

From the repository root, use Python 3.12 and an isolated virtual environment.

```sh
python -m pip install -r src/ledgerlens/app/requirements.txt
streamlit run src/ledgerlens/app/streamlit_app.py
```

## Streamlit Community Cloud

Select `src/ledgerlens/app/community_cloud.py` as the entry point and Python 3.12 in Advanced settings. Community Cloud installs the adjacent `requirements.txt` from the app directory. The entry point always runs in public read-only mode. Fixture mode needs an empty Secrets editor. [Community Cloud dependency placement](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)

To use published demo Gold, paste the [empty secrets template](.streamlit/secrets.toml.example) into Advanced settings. Set `LEDGERLENS_DATA_SOURCE` to `live`, `LEDGERLENS_ENVIRONMENT` to `demo`, and fill in the demo reader's host, client ID, client secret and existing warehouse ID there. For a local Cloud-entry smoke test, use an untracked `.streamlit/secrets.toml` in the repository root. [Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

## Databricks Apps

Deploy the contents of `src/ledgerlens/app` together as the source directory. The included `app.yaml` starts Streamlit. The host provides the process address and port.

To enable the authenticated fixture workbench in Databricks Apps, configure app user authorization and set `LEDGERLENS_ENVIRONMENT` to `dev` or `demo` and `LEDGERLENS_OPERATOR_USER_ID` to the Databricks user ID for Ron in that workspace. The app checks the forwarded user token against the workspace's current-user endpoint and the matching reviewer group. Fixture decisions and batch events are session-local. [Databricks Apps user authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)

## Live data

Keep the fixture source until a verified Gold publication exists. Set `LEDGERLENS_DATA_SOURCE=live`, explicitly select `LEDGERLENS_ENVIRONMENT` and supply the existing warehouse ID through `DATABRICKS_WAREHOUSE_ID`. No other environment is selected on failure. The dashboard shows accuracy as pending when the evaluator has yet to write rows for the pinned run. Confidence shows the distribution of released fields by form type from the pinned publication. The chart reports pending when the publication has no confidence rows. Historical reads require the provisioned Gold `published_*` views.

Databricks Apps provides the workspace URL and the managed app principal's OAuth credentials. That principal needs the contract's Gold reader grants on the selected catalog and explicit warehouse. [Databricks Apps runtime environment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env)

For live Databricks Apps operator actions, enable the user authorization scopes for current-user identity, SQL, files and jobs. Configure the operator ID as for fixture mode and set `LEDGERLENS_PROCESSING_JOB_ID` to the matching deployed Processing job. The app checks the resolved job name and single-run configuration. Batch status comes from `ops.pipeline_runs`, with Jobs API terminal status covering shutdowns before the pipeline records a terminal event. The live workbench uses the delegated reviewer identity to list and download masked pages for the selected document's current extraction. It shows an unavailable state for withheld pages. A local fixture demonstrates the same page selector. [Databricks user authorization scopes](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) · [Jobs run-now idempotency](https://docs.databricks.com/api/jobs/v2/run-now)


## Runtime agent

Fixture mode traces the sample W-2 wages locally. The Databricks Apps deployment needs the Runtime agent package staged alongside the app, the locked MCP dependency, and an explicit `LEDGERLENS_MODEL_ENDPOINT` to call the live panel. It uses the app's existing Gold reader credentials. Community Cloud keeps the live model panel unavailable.

## Fixture media capture

From the repository root, use the locked Playwright environment and install its matching Chromium browser. Install the adjacent app requirements into the capture environment. Run `python src/ledgerlens/app/capture_media.py` to produce five PNG and GIF pairs under `docs/media/`. The script starts a loopback fixture server with a synthetic reviewer and makes no live requests. Use `--output` to select a different destination.

### Hosted Runtime agent source

Stage the hosted App from the repository root so the App source contains both the UI and the locked MCP package.

```bash
app_stage=$(mktemp -d)
python3 scripts/stage_hosted_app.py "$app_stage/ledgerlens"
```

Upload that staged directory as the source for the existing Databricks App when the demo catalog and grants are ready. The stage uses the root MCP pin and the existing App dependencies. Enable the live Runtime agent through the documented hosted endpoint setting and grant the hosted reader identity access to the selected model. The Community Cloud entry point continues to use the App directory and its reader-only requirements.
