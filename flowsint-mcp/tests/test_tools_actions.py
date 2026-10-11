import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from flowsint_mcp.tools import actions

from .conftest import (
    ANYIO,
    API,
    SKETCH,
    call,
    call_error,
    gnode,
    search_page,
    sent_json,
)

pytestmark = ANYIO

STATS = f"{API}/api/sketches/{SKETCH}/stats"
LAUNCH = f"{API}/api/enrichers/domain_to_ip/launch"
SCAN = f"{API}/api/scans/scan-1"


class FakeClock:
    """Monotonic clock advanced by the recorded sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
async def actions_session(client):
    clock = FakeClock()
    mcp = FastMCP("test")
    actions.register(mcp, client, default_timeout=10, sleep=clock.sleep, clock=clock)
    async with create_connected_server_and_client_session(mcp) as s:
        yield s, clock


def stats(domains, ips, edges):
    return {"by_type": {"domain": domains, "ip": ips}, "edges": edges}


async def test_run_enricher_waits_and_reports_new_findings(actions_session, httpx_mock):
    session, clock = actions_session
    httpx_mock.add_response(url=STATS, json=stats(1, 0, 0))
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    httpx_mock.add_response(
        url=SCAN, status_code=404, json={"detail": "Scan not found"}
    )
    httpx_mock.add_response(url=SCAN, json={"status": "PENDING"})
    httpx_mock.add_response(url=SCAN, json={"status": "RUNNING"})
    httpx_mock.add_response(
        url=SCAN, json={"status": "COMPLETED", "details": {"result": ["1.2.3.4"]}}
    )
    httpx_mock.add_response(url=STATS, json=stats(1, 2, 2))

    result = await call(
        session,
        "run_enricher",
        sketch_id=SKETCH,
        enricher="domain_to_ip",
        nodes=["d1"],
        params={"mode": "fast"},
    )

    assert result == {
        "scan_id": "scan-1",
        "status": "COMPLETED",
        "details": {"result": ["1.2.3.4"]},
        "new_nodes_by_type": {"ip": 2},
        "new_edges": 2,
    }
    assert clock.sleeps == [1.0, 1.0, 2.0]
    launch = next(
        r for r in httpx_mock.get_requests() if r.url.path.endswith("/launch")
    )
    assert sent_json(launch) == {
        "node_ids": ["d1"],
        "sketch_id": SKETCH,
        "params": {"mode": "fast"},
    }


async def test_run_enricher_failed_scan(actions_session, httpx_mock):
    session, _ = actions_session
    httpx_mock.add_response(url=STATS, json=stats(1, 0, 0))
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    httpx_mock.add_response(
        url=SCAN, json={"status": "FAILED", "error": "API key missing"}
    )
    result = await call(
        session, "run_enricher", sketch_id=SKETCH, enricher="domain_to_ip", nodes=["d1"]
    )
    assert result == {
        "scan_id": "scan-1",
        "status": "FAILED",
        "error": "API key missing",
    }


async def test_run_enricher_times_out(actions_session, httpx_mock):
    session, clock = actions_session
    httpx_mock.add_response(url=STATS, json=stats(1, 0, 0))
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    httpx_mock.add_response(url=SCAN, json={"status": "RUNNING"}, is_reusable=True)
    result = await call(
        session,
        "run_enricher",
        sketch_id=SKETCH,
        enricher="domain_to_ip",
        nodes=["d1"],
        timeout_s=8,
    )
    assert result["status"] == "RUNNING"
    assert "get_scan" in result["message"]
    assert clock.sleeps == [1.0, 1.0, 2.0, 3.0, 5.0]
    assert clock.now >= 8


async def test_default_timeout_and_long_poll_interval(actions_session, httpx_mock):
    session, clock = actions_session
    httpx_mock.add_response(url=STATS, json=stats(0, 0, 0))
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    httpx_mock.add_response(url=SCAN, json={"status": "PENDING"}, is_reusable=True)
    await call(
        session, "run_enricher", sketch_id=SKETCH, enricher="domain_to_ip", nodes=["d1"]
    )
    # default_timeout=10: 1+1+2+3+5 = 12 >= 10
    assert clock.sleeps == [1.0, 1.0, 2.0, 3.0, 5.0]


async def test_run_enricher_without_waiting(actions_session, httpx_mock):
    session, clock = actions_session
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    result = await call(
        session,
        "run_enricher",
        sketch_id=SKETCH,
        enricher="domain_to_ip",
        nodes=["d1"],
        wait=False,
    )
    assert result == {"scan_id": "scan-1", "status": "QUEUED"}
    assert len(httpx_mock.get_requests()) == 1
    assert clock.sleeps == []


async def test_run_enricher_resolves_node_keys(actions_session, httpx_mock):
    session, _ = actions_session
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/search",
        json=search_page([gnode("d7", "domain", "evil.example")]),
    )
    httpx_mock.add_response(url=LAUNCH, json={"id": "scan-1"})
    await call(
        session,
        "run_enricher",
        sketch_id=SKETCH,
        enricher="domain_to_ip",
        nodes=[{"type": "Domain", "label": "evil.example"}],
        wait=False,
    )
    assert sent_json(httpx_mock.get_requests()[-1])["node_ids"] == ["d7"]


async def test_run_enricher_needs_nodes(actions_session, httpx_mock):
    session, _ = actions_session
    text = await call_error(
        session, "run_enricher", sketch_id=SKETCH, enricher="x", nodes=[]
    )
    assert "at least one node" in text


async def test_launch_failure_is_reported_and_not_retried(actions_session, httpx_mock):
    session, _ = actions_session
    httpx_mock.add_response(url=STATS, json=stats(0, 0, 0))
    httpx_mock.add_response(url=LAUNCH, status_code=503, json={"detail": "busy"})
    text = await call_error(
        session, "run_enricher", sketch_id=SKETCH, enricher="domain_to_ip", nodes=["d1"]
    )
    assert "(503): busy" in text
    assert sum(r.url.path.endswith("/launch") for r in httpx_mock.get_requests()) == 1


async def test_run_flow(actions_session, httpx_mock):
    session, _ = actions_session
    httpx_mock.add_response(url=STATS, json=stats(1, 0, 0))
    httpx_mock.add_response(url=f"{API}/api/flows/flow-1/launch", json={"id": "scan-1"})
    httpx_mock.add_response(url=SCAN, json={"status": "COMPLETED"})
    httpx_mock.add_response(url=STATS, json=stats(1, 0, 0))
    result = await call(
        session, "run_flow", sketch_id=SKETCH, flow_id="flow-1", nodes=["d1"]
    )
    assert result == {
        "scan_id": "scan-1",
        "status": "COMPLETED",
        "new_nodes_by_type": {},
        "new_edges": 0,
    }


@pytest.mark.parametrize(
    "response, expected",
    [
        ((404, {"detail": "nope"}), {"scan_id": "scan-1", "status": "QUEUED"}),
        (
            (200, {"status": "RUNNING", "details": None}),
            {"scan_id": "scan-1", "status": "RUNNING"},
        ),
        (
            (200, {"status": "FAILED", "error": "x" * 400}),
            {
                "scan_id": "scan-1",
                "status": "FAILED",
                "error": "x" * 300 + "… [+100 chars]",
            },
        ),
    ],
)
async def test_get_scan(session, httpx_mock, response, expected):
    status, body = response
    httpx_mock.add_response(url=SCAN, status_code=status, json=body)
    assert await call(session, "get_scan", scan_id="scan-1") == expected
