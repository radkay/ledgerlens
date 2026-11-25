"""The standalone LedgerLens MCP server and its typed tool surface."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from os import environ

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.memory import create_connected_server_and_client_session

from .databricks import ReaderConfig, ReaderWorkspace
from .gold import OUTPUT_FIELDS, GoldTools, PublishedGold

TOOLS = (
    types.Tool(
        name="trace_value",
        description=(
            "Find published Gold values and trace them to source document, field, extraction, and "
            "correction decision IDs. Omit both filters to discover outputs. A row without source "
            "IDs has no contributing field. Results can be truncated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "gold_row_id": {
                    "type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64,
                    "description": "Exact 64-character Gold row ID from a prior result.",
                },
                "output_field": {"type": "string", "enum": list(OUTPUT_FIELDS)},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="documents_needing_review",
        description=(
            "Get the needs_review count for the pinned publication and a separately identified "
            "live list of documents currently needing review."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
)


def create_server(publication: Callable[[], PublishedGold]) -> Server:
    server = Server("ledgerlens", version="0.1.0")

    @server.list_tools()
    async def list_tools():
        return list(TOOLS)

    @server.call_tool(validate_input=True)
    async def call_tool(name, arguments):
        def read():
            if name == "trace_value":
                return publication().trace_value(**arguments)
            if name == "documents_needing_review":
                return publication().documents_needing_review()
            raise ValueError("Unknown LedgerLens MCP tool.")

        return await asyncio.to_thread(read)

    return server


def published_session(publication: PublishedGold):
    """Open an in-process MCP connection pinned to one publication."""
    return create_connected_server_and_client_session(
        create_server(lambda: publication), read_timeout_seconds=timedelta(seconds=65),
    )


async def serve_stdio(config: ReaderConfig):
    workspace = ReaderWorkspace(config)
    server = create_server(GoldTools(config.catalog, workspace.read).pin)
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


def main():
    asyncio.run(serve_stdio(ReaderConfig.from_settings(environ)))


if __name__ == "__main__":
    main()
