"""Run a single managed dbsql MCP comparison query in credentialed dev CI."""

import asyncio
from datetime import timedelta
import json
from os import environ
from pathlib import Path
import re
import sys

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ledgerlens.mcp.databricks import DatabricksError, oauth_token, workspace_origin

CANDIDATE_PATH = "/ai-gateway/mcp-services/system.ai.dbsql"
READ_TOOL = "execute_sql_read_only"
POLL_TOOL = "poll_sql_result"
QUERY = """
SELECT p.run_id
FROM ledgerlens_dev.gold.latest_publication AS p
WHERE EXISTS (
    SELECT 1 FROM ledgerlens_dev.gold.published_lineage AS l WHERE l.run_id = p.run_id
)
LIMIT 1
"""
POLL_LIMIT = 12


class ProbeError(RuntimeError):
    """A managed MCP probe failure with a log-safe description."""


async def _call(session, name, arguments, *, meta=None) -> dict:
    reply = await session.call_tool(name, arguments, meta=meta)
    if (
        reply.isError or len(reply.content) != 1 or not isinstance(reply.content[0], types.TextContent)
        or len(reply.content[0].text) > 65_536
    ):
        raise ProbeError(f"Managed MCP tool {name} did not return a bounded SQL response.")
    try:
        result = json.loads(reply.content[0].text)
    except ValueError:
        raise ProbeError(f"Managed MCP tool {name} returned malformed SQL status.") from None
    if not isinstance(result, dict):
        raise ProbeError(f"Managed MCP tool {name} did not return SQL status.")
    return result


async def query_publication(session: ClientSession, warehouse: str, expected_run_id: str | None) -> dict:
    names = {tool.name for tool in (await session.list_tools()).tools}
    if not {READ_TOOL, POLL_TOOL} <= names:
        raise ProbeError("Managed dbsql MCP is missing the required tools.")
    payload = await _call(session, READ_TOOL, {"query": QUERY}, meta={"warehouse_id": warehouse})
    statement_id = payload.get("statement_id")
    if not isinstance(statement_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,100}", statement_id):
        raise ProbeError("Managed MCP did not return a valid statement ID.")
    polls = 0
    while payload.get("status", {}).get("state") in {"PENDING", "RUNNING"}:
        if polls == POLL_LIMIT:
            raise TimeoutError("Managed MCP SQL did not finish within the polling limit.")
        await asyncio.sleep(2)
        payload = await _call(session, POLL_TOOL, {"statement_id": statement_id})
        polls += 1
    status = payload.get("status", {})
    if status.get("state") != "SUCCEEDED":
        code = status.get("error", {}).get("error_code")
        detail = f" ({code})" if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) else ""
        raise ProbeError(f"Managed MCP SQL did not succeed{detail}.")
    rows = payload.get("result", {}).get("data_typed_array")
    if (
        not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != 1
        or not isinstance(rows[0][0], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", rows[0][0])
    ):
        raise ProbeError("Managed MCP did not return one published run with lineage.")
    run_id = rows[0][0]
    if expected_run_id and run_id != expected_run_id:
        raise ProbeError("Managed MCP used a different publication from the dev smoke.")
    return {"managed_tool": READ_TOOL, "run_id": run_id, "statement_id": statement_id, "polls": polls, "row_count": 1}


async def run(settings) -> dict:
    host = workspace_origin(settings.get("DATABRICKS_HOST", ""))
    endpoint = settings.get("LEDGERLENS_MANAGED_MCP_URL", "")
    warehouse = settings.get("DATABRICKS_WAREHOUSE_ID", "")
    if (
        settings.get("LEDGERLENS_CATALOG") != "ledgerlens_dev" or endpoint != host + CANDIDATE_PATH
        or not re.fullmatch(r"[a-f0-9]{16}", warehouse)
    ):
        raise ProbeError("Configure the explicit dev Unity Gateway MCP URL, catalog, and warehouse.")
    token, _ = await asyncio.to_thread(
        oauth_token, host, settings.get("DATABRICKS_CLIENT_ID", ""),
        settings.get("DATABRICKS_CLIENT_SECRET", ""), scope="ai-gateway",
    )
    async with streamablehttp_client(
        endpoint, headers={"Authorization": f"Bearer {token}"},
        timeout=20, sse_read_timeout=25, terminate_on_close=True,
    ) as (incoming, outgoing, _):
        async with ClientSession(incoming, outgoing, read_timeout_seconds=timedelta(seconds=25)) as session:
            await session.initialize()
            result = await query_publication(session, warehouse, settings.get("LEDGERLENS_EXPECTED_RUN_ID"))
    return {"managed_endpoint": endpoint, **result}


async def verify():
    async with asyncio.timeout(90):
        return await run(environ)


def _failure_code(error: Exception) -> str:
    if isinstance(error, ExceptionGroup):
        return ", ".join(dict.fromkeys(_failure_code(child) for child in error.exceptions))
    if isinstance(error, (ProbeError, DatabricksError)):
        return str(error)
    status_code = getattr(getattr(error, "response", None), "status_code", None)
    return f"HTTP {status_code}" if type(status_code) is int else type(error).__name__


if __name__ == "__main__":
    try:
        evidence = asyncio.run(verify())
    except Exception as error:
        raise SystemExit(f"Managed dbsql MCP verification failed ({_failure_code(error)}).") from None
    print(json.dumps(evidence), flush=True)
