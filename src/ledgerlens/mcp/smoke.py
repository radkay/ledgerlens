"""Run the custom MCP and model checks from the credentialed dev integration."""

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from os import environ
import re

from mcp import ClientSession, types

from .agent import answer_with_tools
from .databricks import ReaderWorkspace, RuntimeConfig
from .gold import GoldTools
from .server import published_session


@dataclass(frozen=True)
class SmokeEvidence:
    endpoint: str
    run_id: str
    gold_query_rows: int
    lineage_rows: int
    agent_tools: tuple[str, ...]


async def trace(session: ClientSession, run_id: str, **arguments) -> list[dict]:
    result = await session.call_tool("trace_value", arguments)
    data = result.structuredContent
    if result.isError and len(result.content) == 1 and isinstance(result.content[0], types.TextContent):
        detail = result.content[0].text
        if re.fullmatch(
            r"(?:The bounded Gold statement did not succeed \(SQL"
            r"|Databricks request failed \(HTTP [0-9]{3})(?:, [A-Z][A-Z0-9_]{1,79})?\)\.",
            detail,
        ):
            raise RuntimeError(detail)
    if result.isError or not isinstance(data, dict) or data.get("run_id") != run_id or not data.get("rows"):
        raise RuntimeError("Custom MCP did not return the expected Gold evidence.")
    if "gold_row_id" in arguments and data.get("truncated"):
        raise RuntimeError("Custom MCP returned a truncated reverse-lineage trace.")
    return data["rows"]


async def verify_runtime(config: RuntimeConfig, *, expected_run_id: str | None = None) -> SmokeEvidence:
    if config.reader.catalog != "ledgerlens_dev":
        raise ValueError("The Runtime agent smoke requires the development catalog.")
    workspace = ReaderWorkspace(config.reader)
    gold = await asyncio.to_thread(GoldTools(config.reader.catalog, workspace.read).pin)
    if expected_run_id is not None and gold.run_id != expected_run_id:
        raise RuntimeError("The reader has a different published run from the dev smoke.")
    async with published_session(gold) as session:
        return await _verify_session(config, workspace, session, gold.run_id)


async def _verify_session(config, workspace, session, run_id) -> SmokeEvidence:
    values = await trace(session, run_id, output_field="wages")
    wage = values[0]
    row_id = wage["gold_row_id"]
    links = await trace(session, run_id, gold_row_id=row_id, output_field="wages")
    if any(
        row["gold_row_id"] != row_id or row["output_field"] != "wages"
        or not all(row[key] for key in ("doc_id", "field_id", "extraction_id"))
        for row in links
    ):
        raise RuntimeError("Custom MCP did not return the expected reverse-lineage links.")
    print(json.dumps({
        "custom_mcp": "passed", "run_id": run_id, "gold_query_rows": len(values), "lineage_rows": len(links),
    }), flush=True)

    question = f"What is the published wage amount for Gold row {row_id}? Use trace_value."
    answer = await answer_with_tools(
        question, session, lambda request: workspace.chat(config.endpoint, request), run_id,
    )
    if not answer.grounded:
        raise RuntimeError(f"Runtime agent could not answer the smoke question. {answer.text}")
    amounts = re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])", answer.text)
    if not any(
        call.succeeded and call.name == "trace_value" and call.run_id == run_id
        and call.arguments.get("gold_row_id") == row_id and call.arguments.get("output_field") in (None, "wages")
        for call in answer.tool_calls
    ) or not any(Decimal(value.replace(",", "")) == Decimal(wage["output_value"]) for value in amounts):
        raise RuntimeError("Runtime agent did not return the traced published wage.")
    return SmokeEvidence(
        config.endpoint, run_id, len(values), len(links), tuple(call.name for call in answer.tool_calls),
    )


def _error_message(error: Exception) -> str:
    if isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
        return _error_message(error.exceptions[0])
    if isinstance(error, (OSError, LookupError, RuntimeError, ValueError)):
        return str(error)
    return type(error).__name__


def main():
    try:
        evidence = asyncio.run(verify_runtime(
            RuntimeConfig.from_settings(environ), expected_run_id=environ.get("LEDGERLENS_EXPECTED_RUN_ID"),
        ))
    except Exception as error:
        raise SystemExit(f"Runtime agent smoke failed. {_error_message(error)}") from None
    print(json.dumps(asdict(evidence)), flush=True)


if __name__ == "__main__":
    main()
