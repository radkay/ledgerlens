# LedgerLens MCP and Runtime agent evidence

The Runtime agent requires `LEDGERLENS_MODEL_ENDPOINT=databricks-gpt-oss-20b`. Ron selected the hosted endpoint called by the November 23 capability probe. The original notebook and output are pending in [#12](https://github.com/radkay/ledgerlens/issues/12). The Runtime agent implementation has a credential-free synthetic transcript test and a verified dev reader invocation.

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

The dev reader completed the bounded Gold query, reverse-lineage trace, and Runtime agent answer through `databricks-gpt-oss-20b`. Publication `f804f31bad34c3e0ecbaf86f44991f1630098215eac07bdecfc998a3adb286e5` returned two Gold query rows and one lineage row. The Runtime agent used `trace_value`. The managed comparison failed in its separate step. [Dev integration run #48](https://github.com/radkay/ledgerlens/actions/runs/45627924094)

## Managed dbsql comparison

The credentialed probe reached tool discovery at `https://dbc-d6b23c20-689f.cloud.databricks.com/ai-gateway/mcp-services/system.ai.dbsql`. Runs #48 and #49 stopped before submitting SQL because discovery did not satisfy the earlier check for both `execute_sql_read_only` and `poll_sql_result`. The old probe omitted the returned tool names, so those runs cannot establish which names the service advertised. [Dev run #48](https://github.com/radkay/ledgerlens/actions/runs/45627924094), [dev run #49](https://github.com/radkay/ledgerlens/actions/runs/45629452771)

The dev workflow uses the existing `ledgerlens-dev-ci` principal with the `ai-gateway` OAuth scope. Pass the explicit target in `LEDGERLENS_MANAGED_MCP_URL`, the existing `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` and `DATABRICKS_WAREHOUSE_ID`, and `LEDGERLENS_CATALOG=ledgerlens_dev`. Pass the existing smoke's publication as `LEDGERLENS_EXPECTED_RUN_ID` when available.

```sh
UV_CACHE_DIR=.uv-cache uv run --no-sync python docs/evidence/managed_dbsql_mcp.py
```

The script prints a `managed_tools` JSON diagnostic with the discovered tool names before calling SQL. It omits tool descriptions, schemas, cursors and credentials. It prefers the documented `execute_sql_read_only` tool when listed and can use the documented `execute_sql` tool for the same fixed read-only query. The query checks for Gold lineage in the latest publication and limits the response to one row. The probe requires the documented `poll_sql_result` name, passes the warehouse explicitly, and bounds polling by count and deadline. Success logs the verified endpoint, selected tool, publication run, statement ID, poll count and row count. [Databricks SQL MCP tool list and example](https://docs.databricks.com/aws/en/agents/mcp-tools/databricks-sql#tools)

| Surface | Caller | Query boundary |
|---|---|---|
| Custom LedgerLens MCP | Dev reader | Two domain tools, fixed parameterized statements and fixed response bounds |
| Managed dbsql MCP | Dev integration | Documented SQL execution tool under Unity Catalog permissions. The probe supplies a fixed read-only query and an explicit warehouse. |

The revised discovery and tool selection passed synthetic tests. The next eligible dev push will capture the workspace's actual list and attempt the query if the documented tools are present. Managed SQL execution remains unverified. No live rerun was started for this revision. Keep the November 23 artifact intake tied to [#12](https://github.com/radkay/ledgerlens/issues/12).
