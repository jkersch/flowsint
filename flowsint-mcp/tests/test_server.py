import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from flowsint_mcp import __main__ as entry
from flowsint_mcp.config import Settings
from flowsint_mcp.server import create_server

from .conftest import ANYIO, API

pytestmark = ANYIO

READ_TOOLS = {
    "list_investigations",
    "list_sketches",
    "get_sketch",
    "list_types",
    "list_enrichers",
    "list_flows",
    "search_nodes",
    "search_investigation",
    "get_node",
    "sketch_summary",
    "get_scan",
}
WRITE_TOOLS = {"add_node", "add_relationship", "edit_node", "import_entities"}
RUN_TOOLS = {"run_enricher", "run_flow"}


async def tools_of(settings):
    async with create_connected_server_and_client_session(
        create_server(settings)
    ) as session:
        return {t.name: t for t in (await session.list_tools()).tools}


async def test_full_tool_catalogue_and_annotations():
    tools = await tools_of(Settings(api_url=API, api_token="t"))
    assert set(tools) == READ_TOOLS | WRITE_TOOLS | RUN_TOOLS

    for name in READ_TOOLS:
        assert tools[name].annotations.readOnlyHint is True, name
    for name in WRITE_TOOLS:
        a = tools[name].annotations
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint) == (
            False,
            False,
            True,
        )
    for name in RUN_TOOLS:
        a = tools[name].annotations
        assert (a.readOnlyHint, a.openWorldHint, a.idempotentHint) == (
            False,
            True,
            False,
        )
    assert all(t.description for t in tools.values())


async def test_read_only_mode_registers_no_write_or_run_tools():
    tools = await tools_of(Settings(api_url=API, api_token="t", read_only=True))
    assert set(tools) == READ_TOOLS


async def test_server_announces_instructions():
    async with create_connected_server_and_client_session(
        create_server(Settings(api_url=API, api_token="t"))
    ) as session:
        assert session  # handshake succeeded
    server = create_server(Settings(api_url=API, api_token="t"))
    assert "untrusted data" in server.instructions


def test_main_exits_2_on_config_error(monkeypatch, capsys):
    for var in ("FLOWSINT_API_TOKEN", "FLOWSINT_EMAIL", "FLOWSINT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    assert entry.main() == 2
    assert "flowsint-mcp: Set FLOWSINT_API_TOKEN" in capsys.readouterr().err


def test_main_runs_configured_transport(monkeypatch):
    monkeypatch.setenv("FLOWSINT_API_TOKEN", "t")
    ran = []
    monkeypatch.setattr(
        entry,
        "create_server",
        lambda s: type("S", (), {"run": lambda self, t: ran.append(t)})(),
    )
    assert entry.main() == 0
    assert ran == ["stdio"]


@pytest.mark.parametrize("module", ["flowsint_mcp", "flowsint_mcp.tools"])
def test_packages_import(module):
    __import__(module)


async def test_stdio_entry_point_serves_tools():
    """Start the real process the way an MCP client does and talk to it."""
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flowsint_mcp"],
        env={
            **os.environ,
            "FLOWSINT_API_URL": API,
            "FLOWSINT_API_TOKEN": "t",
            "FLOWSINT_MCP_READ_ONLY": "true",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
    assert info.serverInfo.name == "flowsint"
    assert tools == READ_TOOLS
