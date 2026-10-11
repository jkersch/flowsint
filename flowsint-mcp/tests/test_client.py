import httpx
import pytest

from flowsint_mcp.client import FlowsintAPIError, FlowsintClient
from flowsint_mcp.config import Settings

from .conftest import ANYIO, API, SKETCH, sent_json

pytestmark = ANYIO


def login_settings():
    return Settings(api_url=API, email="me@x", password="pw")


async def test_sends_bearer_token(client, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/investigations", json=[])
    assert await client.list_investigations() == []
    assert httpx_mock.get_request().headers["Authorization"] == "Bearer tok"


async def test_empty_body_returns_none(client, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/investigations", content=b"")
    assert await client.list_investigations() is None


async def test_logs_in_first_when_no_token(httpx_mock, sleeps):
    client = FlowsintClient(login_settings(), sleep=sleeps)
    httpx_mock.add_response(url=f"{API}/api/auth/token", json={"access_token": "jwt1"})
    httpx_mock.add_response(url=f"{API}/api/flows", json=[])

    await client.list_flows()

    login, call = httpx_mock.get_requests()
    assert login.content == b"username=me%40x&password=pw"
    assert call.headers["Authorization"] == "Bearer jwt1"


async def test_expired_token_relogs_once(httpx_mock, sleeps):
    settings = Settings(api_url=API, api_token="old", email="me@x", password="pw")
    client = FlowsintClient(settings, sleep=sleeps)
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=401)
    httpx_mock.add_response(url=f"{API}/api/auth/token", json={"access_token": "new"})
    httpx_mock.add_response(url=f"{API}/api/flows", json=[])

    assert await client.list_flows() == []
    auth = [r.headers.get("Authorization") for r in httpx_mock.get_requests()]
    assert auth == ["Bearer old", None, "Bearer new"]


async def test_second_401_after_relogin_is_an_error(httpx_mock, sleeps):
    client = FlowsintClient(login_settings(), sleep=sleeps)
    httpx_mock.add_response(url=f"{API}/api/auth/token", json={"access_token": "a"})
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=401)
    httpx_mock.add_response(url=f"{API}/api/auth/token", json={"access_token": "b"})
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=401)

    with pytest.raises(FlowsintAPIError, match="Authentication failed") as e:
        await client.list_flows()
    assert e.value.status == 401


async def test_401_without_login_credentials_is_an_error(client, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=401)
    with pytest.raises(FlowsintAPIError, match="Authentication failed"):
        await client.list_flows()


async def test_failed_login(httpx_mock, sleeps):
    client = FlowsintClient(login_settings(), sleep=sleeps)
    httpx_mock.add_response(url=f"{API}/api/auth/token", status_code=400)
    with pytest.raises(FlowsintAPIError, match="Login failed"):
        await client.list_flows()


async def test_login_when_api_is_down(httpx_mock, sleeps):
    client = FlowsintClient(login_settings(), sleep=sleeps)
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(FlowsintAPIError, match="Cannot reach the Flowsint API"):
        await client.login()


async def test_gateway_errors_are_retried_with_backoff(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=502)
    httpx_mock.add_response(url=f"{API}/api/flows", status_code=503)
    httpx_mock.add_response(url=f"{API}/api/flows", json=[{"id": "f"}])
    assert await client.list_flows() == [{"id": "f"}]
    assert sleeps == [0.5, 1.0]


async def test_gateway_errors_give_up_after_three_retries(client, httpx_mock, sleeps):
    for _ in range(4):
        httpx_mock.add_response(
            url=f"{API}/api/flows", status_code=504, json={"detail": "timeout"}
        )
    with pytest.raises(FlowsintAPIError, match=r"Flowsint API error \(504\): timeout"):
        await client.list_flows()
    assert sleeps == [0.5, 1.0, 2.0]


async def test_connection_errors_are_retried(client, httpx_mock, sleeps):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    httpx_mock.add_response(url=f"{API}/api/flows", json=[])
    assert await client.list_flows() == []
    assert sleeps == [0.5]


async def test_unreachable_api(client, httpx_mock, sleeps):
    for _ in range(4):
        httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(
        FlowsintAPIError, match=f"Cannot reach the Flowsint API at {API}"
    ):
        await client.list_flows()


async def test_launches_are_never_retried(client, httpx_mock, sleeps):
    httpx_mock.add_response(status_code=502)
    with pytest.raises(FlowsintAPIError, match="502"):
        await client.launch_enricher("e", SKETCH, ["n1"])
    assert len(httpx_mock.get_requests()) == 1
    assert sleeps == []


async def test_launch_connection_error_is_not_retried(client, httpx_mock, sleeps):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(FlowsintAPIError, match="Cannot reach"):
        await client.launch_flow("f", SKETCH, ["n1"])
    assert sleeps == []


@pytest.mark.parametrize(
    "status, body, message",
    [
        (403, {"detail": "Forbidden"}, "No access: your Flowsint user lacks"),
        (
            403,
            {"detail": "This API token lacks the 'write' scope"},
            "No access: This API token lacks the 'write' scope.",
        ),
        (404, {"detail": "Sketch not found"}, "Not found: Sketch not found"),
        (
            400,
            {"detail": "Unknown type(s): X"},
            r"Invalid request: Unknown type\(s\): X",
        ),
        (
            422,
            {"detail": [{"loc": ["body", "limit"], "msg": "too big"}]},
            "Invalid request: limit: too big",
        ),
        (500, {"detail": "Failed to search nodes"}, r"\(500\): Failed to search"),
        (418, "teapot", r"\(418\): teapot"),
    ],
)
async def test_error_mapping(client, httpx_mock, status, body, message):
    if isinstance(body, str):
        httpx_mock.add_response(status_code=status, text=body)
    else:
        httpx_mock.add_response(status_code=status, json=body)
    with pytest.raises(FlowsintAPIError, match=message) as e:
        await client.get_sketch(SKETCH)
    assert e.value.status == status


async def test_error_with_non_json_body(client, httpx_mock):
    httpx_mock.add_response(status_code=500, text="<html>boom</html>")
    with pytest.raises(FlowsintAPIError, match="boom"):
        await client.get_sketch(SKETCH)


async def test_element_ids_are_quoted_in_paths(client, httpx_mock):
    httpx_mock.add_response(json={"nds": [], "rls": []})
    await client.get_neighbors(SKETCH, "4:abc/def:12")
    assert (
        httpx_mock.get_request()
        .url.raw_path.decode()
        .endswith("/nodes/4%3Aabc%2Fdef%3A12")
    )


async def test_search_drops_unset_criteria(client, httpx_mock):
    httpx_mock.add_response(json={"nodes": [], "next_cursor": None})
    await client.search_nodes(SKETCH, types=["Domain"], query=None, limit=5)
    assert sent_json(httpx_mock.get_request()) == {"types": ["Domain"], "limit": 5}


async def test_import_sends_form_payload(client, httpx_mock):
    httpx_mock.add_response(json={"status": "completed"})
    await client.import_entities(SKETCH, [{"id": "a"}], [])
    request = httpx_mock.get_request()
    assert request.url.path == f"/api/sketches/{SKETCH}/import/execute"
    assert request.content.startswith(b"entity_mappings_json=")


async def test_scan_404_means_queued(client, httpx_mock):
    httpx_mock.add_response(status_code=404, json={"detail": "Scan not found"})
    assert await client.get_scan("scan-1") is None


async def test_scan_other_errors_propagate(client, httpx_mock):
    httpx_mock.add_response(status_code=403, json={"detail": "Forbidden"})
    with pytest.raises(FlowsintAPIError, match="No access"):
        await client.get_scan("scan-1")


async def test_launch_payloads(client, httpx_mock):
    httpx_mock.add_response(json={"id": "scan-1"})
    httpx_mock.add_response(json={"id": "scan-2"})
    assert await client.launch_enricher("dns", SKETCH, ["n1"], {"k": 1}) == "scan-1"
    assert await client.launch_flow("flow-1", SKETCH, ["n1"]) == "scan-2"
    enricher, flow = httpx_mock.get_requests()
    assert enricher.url.path == "/api/enrichers/dns/launch"
    assert sent_json(enricher) == {
        "node_ids": ["n1"],
        "sketch_id": SKETCH,
        "params": {"k": 1},
    }
    assert flow.url.path == "/api/flows/flow-1/launch"
    assert sent_json(flow) == {"node_ids": ["n1"], "sketch_id": SKETCH}


async def test_default_http_client_and_close(settings):
    client = FlowsintClient(settings)
    assert str(client._http.base_url) == f"{API}"
    await client.aclose()
    assert client._http.is_closed


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def in_mcp_request(headers):
    """Simulate being inside an MCP request handled over HTTP."""
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    return request_ctx.set(
        RequestContext(
            request_id=1,
            meta=None,
            session=None,
            lifespan_context=None,
            request=FakeRequest(headers) if headers is not None else None,
        )
    )


def http_client(sleeps):
    return FlowsintClient(Settings(api_url=API, transport="http"), sleep=sleeps)


async def test_http_mode_forwards_the_callers_token(httpx_mock, sleeps):
    from mcp.server.lowlevel.server import request_ctx

    client = http_client(sleeps)
    httpx_mock.add_response(url=f"{API}/api/flows", json=[])
    httpx_mock.add_response(url=f"{API}/api/flows", json=[])

    for token in ("fsk_alice", "fsk_bob"):
        reset = in_mcp_request({"authorization": f"Bearer {token}"})
        try:
            await client.list_flows()
        finally:
            request_ctx.reset(reset)

    sent = [r.headers["Authorization"] for r in httpx_mock.get_requests()]
    assert sent == ["Bearer fsk_alice", "Bearer fsk_bob"]


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {},
        {"authorization": ""},
        {"authorization": "Basic abc"},
        {"authorization": "Bearer   "},
    ],
)
async def test_http_mode_without_bearer_fails_before_calling_api(
    httpx_mock, sleeps, headers
):
    from mcp.server.lowlevel.server import request_ctx

    reset = in_mcp_request(headers)
    try:
        with pytest.raises(FlowsintAPIError, match="Authorization: Bearer") as e:
            await http_client(sleeps).list_flows()
    finally:
        request_ctx.reset(reset)
    assert e.value.status == 401
    assert httpx_mock.get_requests() == []


async def test_http_mode_outside_a_request(httpx_mock, sleeps):
    with pytest.raises(FlowsintAPIError, match="Authorization: Bearer"):
        await http_client(sleeps).list_flows()


async def test_http_mode_does_not_relogin_on_401(httpx_mock, sleeps):
    from mcp.server.lowlevel.server import request_ctx

    httpx_mock.add_response(url=f"{API}/api/flows", status_code=401)
    reset = in_mcp_request({"authorization": "Bearer fsk_revoked"})
    try:
        with pytest.raises(FlowsintAPIError, match="Authentication failed"):
            await http_client(sleeps).list_flows()
    finally:
        request_ctx.reset(reset)
    assert len(httpx_mock.get_requests()) == 1
