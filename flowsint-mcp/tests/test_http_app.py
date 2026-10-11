"""The shared HTTP server: bearer pass-through, health, statelessness."""

import anyio
import pytest
from starlette.testclient import TestClient

from flowsint_mcp import __main__ as entry
from flowsint_mcp.client import FlowsintClient
from flowsint_mcp.config import Settings
from flowsint_mcp.server import create_http_app

from .conftest import API, SKETCH, gnode, search_page

HEADERS = {"Accept": "application/json, text/event-stream"}


def rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


@pytest.fixture
def make_app():
    def make(**overrides):
        settings = Settings(api_url=API, transport="http", **overrides)
        return TestClient(create_http_app(settings, FlowsintClient(settings)))

    return make


@pytest.fixture
def http(make_app):
    with make_app() as client:
        yield client


def call(http, token, method, params=None):
    return http.post(
        "/mcp",
        json=rpc(method, params),
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
    )


def test_health_needs_no_token(http):
    res = http.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "auth", [None, "", "Basic dXNlcjpwdw==", "Bearer", "Bearer   "]
)
def test_mcp_requires_a_bearer_token(http, httpx_mock, auth):
    headers = dict(HEADERS)
    if auth is not None:
        headers["Authorization"] = auth
    res = http.post("/mcp", json=rpc("tools/list"), headers=headers)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert "Authorization: Bearer" in res.json()["message"]
    assert httpx_mock.get_requests() == []


def test_lists_tools_without_a_session(http):
    res = call(http, "fsk_alice", "tools/list")
    assert res.status_code == 200
    names = {t["name"] for t in res.json()["result"]["tools"]}
    assert {"search_nodes", "run_enricher", "add_node"} <= names


def test_each_caller_acts_with_their_own_token(http, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/search",
        json=search_page([gnode("d1", "domain", "evil.example")]),
        is_reusable=True,
    )
    for token in ("fsk_alice", "fsk_bob"):
        res = call(
            http,
            token,
            "tools/call",
            {"name": "search_nodes", "arguments": {"sketch_id": SKETCH}},
        )
        result = res.json()["result"]
        assert result["isError"] is False
        assert "evil.example" in result["content"][0]["text"]

    assert [r.headers["Authorization"] for r in httpx_mock.get_requests()] == [
        "Bearer fsk_alice",
        "Bearer fsk_bob",
    ]


def test_rejected_token_surfaces_as_tool_error(http, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/investigations", status_code=401)
    res = call(
        http,
        "fsk_revoked",
        "tools/call",
        {"name": "list_investigations", "arguments": {}},
    )
    result = res.json()["result"]
    assert result["isError"] is True
    assert "Authentication failed" in result["content"][0]["text"]


def test_read_only_over_http(make_app):
    with make_app(read_only=True) as http:
        tools = call(http, "fsk_x", "tools/list").json()["result"]["tools"]
    assert "add_node" not in {t["name"] for t in tools}


def test_allowed_hosts_block_other_hosts(make_app):
    with make_app(allowed_hosts=["mcp.example.com"]) as http:
        blocked = call(http, "fsk_x", "tools/list")
        allowed = http.post(
            "/mcp",
            json=rpc("tools/list"),
            headers={
                **HEADERS,
                "Authorization": "Bearer fsk_x",
                "Host": "mcp.example.com",
            },
        )
    assert blocked.status_code == 421
    assert allowed.status_code == 200


def test_main_serves_http_with_uvicorn(monkeypatch):
    for var in ("FLOWSINT_API_TOKEN", "FLOWSINT_EMAIL", "FLOWSINT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FLOWSINT_MCP_TRANSPORT", "http")
    monkeypatch.setenv("FLOWSINT_MCP_PORT", "6000")
    served = {}
    monkeypatch.setattr(
        entry.uvicorn,
        "run",
        lambda app, host, port: served.update(app=app, host=host, port=port),
    )
    assert entry.main() == 0
    assert (served["host"], served["port"]) == ("0.0.0.0", 6000)
    assert callable(served["app"])


async def _serve(port):
    """Start `python -m flowsint_mcp` in HTTP mode and wait for /health."""
    import os
    import subprocess
    import sys
    import time

    import httpx

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLOWSINT_API_TOKEN", "FLOWSINT_EMAIL", "FLOWSINT_PASSWORD")
    }
    env.update(
        FLOWSINT_API_URL=API,
        FLOWSINT_MCP_TRANSPORT="http",
        FLOWSINT_MCP_HOST="127.0.0.1",
        FLOWSINT_MCP_PORT=str(port),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "flowsint_mcp"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    async with httpx.AsyncClient() as http:
        while time.monotonic() < deadline:
            try:
                if (
                    await http.get(f"http://127.0.0.1:{port}/health")
                ).status_code == 200:
                    return proc
            except httpx.TransportError:
                pass
            await anyio.sleep(0.1)
    proc.kill()
    raise AssertionError("flowsint-mcp did not start")


@pytest.mark.anyio
async def test_real_http_server_with_streamable_http_client():
    """The way a remote MCP client connects: real process, real HTTP."""
    import socket

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = await _serve(port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx.AsyncClient() as http:
            anonymous = await http.post(url, json=rpc("tools/list"), headers=HEADERS)
        assert anonymous.status_code == 401

        async with streamablehttp_client(
            url, headers={"Authorization": "Bearer fsk_test"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
        assert "search_nodes" in tools
    finally:
        proc.terminate()
        proc.wait(timeout=10)
