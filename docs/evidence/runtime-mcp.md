# LedgerLens MCP and Runtime agent evidence

The Runtime agent requires `LEDGERLENS_MODEL_ENDPOINT=databricks-gpt-oss-20b`. Ron selected the hosted endpoint called by the November 23 capability probe. The original notebook and output are pending in [#12](https://github.com/radkay/ledgerlens/issues/12). The Runtime agent implementation has a credential-free synthetic transcript test. The dev reader invocation is pending.

## Approved public app behavior

The public Runtime agent will answer preset questions against a pinned Gold publication. Each answer is cached for its preset and publication, so repeat selections reuse that answer. A daily cap bounds live generation. The MCP lane owns implementation and live verification of this public behavior. [Decision D-026](../DECISIONS.md)

## Custom MCP smoke

The custom server offers `trace_value` and `documents_needing_review` over MCP stdio. The Runtime agent connects to the same server in process and resolves one publication for the question. The tools accept domain parameters. The server binds them to fixed SQL statements over Gold serving views. The review tool returns a run-stamped publication count and a separate live document list. Both paths cap responses at 25 rows and report truncation.

Point a local MCP client at `python -m ledgerlens.mcp.server` in the locked environment. Set `PYTHONPATH=src` from the repository root and provision the server process with the reader settings below. The standalone server uses the same catalog and warehouse configuration as the Runtime agent.

From the repository root, the credentialed dev workflow runs the following after the existing smoke confirms a publication. Pass the existing workspace host and warehouse, `LEDGERLENS_CATALOG=ledgerlens_dev`, `LEDGERLENS_MODEL_ENDPOINT=databricks-gpt-oss-20b`, and the dev reader application ID and secret as `DATABRICKS_READER_CLIENT_ID` and `DATABRICKS_READER_CLIENT_SECRET`. Set `LEDGERLENS_EXPECTED_RUN_ID` to the publication established by the main smoke when invoking the check from a separate process.

```sh
UV_CACHE_DIR=.uv-cache uv sync --locked
UV_CACHE_DIR=.uv-cache PYTHONPATH=src uv run --no-sync python -m ledgerlens.mcp.smoke
```

The check discovers a published Gold wage, traces the exact row and field to document, field and extraction IDs, then asks the hosted model for the traced wage. It requires a successful `trace_value` call for the same row and publication and checks the answer against the Gold value. The output records the publication run, row counts, endpoint and tool names. It omits the wage, source identifiers, chat transcript and credentials.

The credentialed dev environment did not have the reader application ID or secret when the smoke was prepared. First is coordinating the protected workflow and contract changes in [#57](https://github.com/radkay/ledgerlens/issues/57). Once the reader can authenticate and query Gold, an invocation HTTP 403 indicates a serving permission to investigate. If the endpoint ACL lacks an applicable grant, the workspace administrator can add `CAN_QUERY` on `databricks-gpt-oss-20b` for the application ID of `ledgerlens-dev-reader`. The permissions API takes the resolved endpoint ID and an additive update. [Databricks serving endpoint permissions](https://docs.databricks.com/aws/en/machine-learning/model-serving/manage-serving-endpoints), [Permissions API](https://docs.databricks.com/api/access-management/v1/update-object-permissions).

## Managed dbsql comparison

The configured target for the credentialed check is `https://dbc-d6b23c20-689f.cloud.databricks.com/ai-gateway/mcp-services/system.ai.dbsql`. This URL combines the configured dev workspace origin with the documented Unity Gateway service path. A credentialed call remains pending. [Databricks SQL MCP documentation](https://docs.databricks.com/aws/en/agents/mcp-tools/databricks-sql).

The dev workflow uses the existing `ledgerlens-dev-ci` principal with the `ai-gateway` OAuth scope. Pass the explicit target in `LEDGERLENS_MANAGED_MCP_URL`, the existing `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` and `DATABRICKS_WAREHOUSE_ID`, and `LEDGERLENS_CATALOG=ledgerlens_dev`. Pass the existing smoke's publication as `LEDGERLENS_EXPECTED_RUN_ID` when available.

```sh
UV_CACHE_DIR=.uv-cache uv run --no-sync python docs/evidence/managed_dbsql_mcp.py
```

The script calls `execute_sql_read_only` with one fixed query that checks for Gold lineage in the latest publication and limits the response to one row. It passes the warehouse explicitly and polls `poll_sql_result` under both a count and a deadline. Success logs the verified endpoint, tool, publication run, statement ID, poll count and row count.

| Surface | Caller | Query boundary |
|---|---|---|
| Custom LedgerLens MCP | Dev reader | Two domain tools, fixed parameterized statements and fixed response bounds |
| Managed dbsql MCP | Dev integration | Managed read-only SQL tool under Unity Catalog permissions. The check supplies a fixed query and an explicit warehouse. |

The managed service's Unity Gateway access and preview availability remain unverified in this workspace. A permission failure should identify the missing privilege before any change to the integration identity. Attach the credentialed workflow run and the emitted summaries to this page after success. Keep the November 23 artifact intake tied to [#12](https://github.com/radkay/ledgerlens/issues/12).
