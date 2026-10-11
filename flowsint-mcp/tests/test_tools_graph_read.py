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

SEARCH = f"{API}/api/sketches/{SKETCH}/nodes/search"


async def test_search_nodes(session, httpx_mock):
    httpx_mock.add_response(
        url=SEARCH,
        json=search_page(
            [gnode("4:x:1", "phishingpage", "https://evil/", status="VERIFIED")],
            cursor="4:x:1",
        ),
    )
    result = await call(
        session,
        "search_nodes",
        sketch_id=SKETCH,
        types=["PhishingPage"],
        query="evil",
        filters={"status": "VERIFIED", "active": True},
        limit=1,
    )
    assert result == {
        "nodes": [
            {
                "ref": "4:x:1",
                "type": "phishingpage",
                "label": "https://evil/",
                "props": {"status": "VERIFIED"},
            }
        ],
        "next_cursor": "4:x:1",
    }
    assert sent_json(httpx_mock.get_request()) == {
        "types": ["PhishingPage"],
        "query": "evil",
        "filters": {"status": "VERIFIED", "active": True},
        "limit": 1,
    }


async def test_search_defaults(session, httpx_mock):
    httpx_mock.add_response(url=SEARCH, json=search_page([]))
    await call(session, "search_nodes", sketch_id=SKETCH)
    assert sent_json(httpx_mock.get_request()) == {"limit": 25}


async def test_search_limit_is_bounded_by_schema(session, httpx_mock):
    text = await call_error(session, "search_nodes", sketch_id=SKETCH, limit=500)
    assert "limit" in text
    assert httpx_mock.get_requests() == []


async def test_search_api_error_is_a_tool_error(session, httpx_mock):
    httpx_mock.add_response(
        url=SEARCH, status_code=400, json={"detail": "Unknown type(s): Nope"}
    )
    text = await call_error(session, "search_nodes", sketch_id=SKETCH, types=["Nope"])
    assert "Invalid request: Unknown type(s): Nope" in text


def neighbors(center, *pairs):
    """API neighbors payload: center node plus (edge, other node) pairs."""
    return {
        "nds": [center] + [other for _, other in pairs],
        "rls": [edge for edge, _ in pairs],
    }


async def test_get_node_groups_neighbors_by_relationship(session, httpx_mock):
    center = gnode("d1", "domain", "evil.example")
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/d1",
        json=neighbors(
            center,
            (
                {"source": "d1", "target": "i1", "label": "RESOLVES_TO"},
                gnode("i1", "ip", "1.2.3.4"),
            ),
            (
                {"source": "d1", "target": "i2", "label": "RESOLVES_TO"},
                gnode("i2", "ip", "5.6.7.8"),
            ),
            (
                {"source": "p1", "target": "d1", "label": "HOSTED_ON"},
                gnode("p1", "phishingpage", "https://evil.example/"),
            ),
            (
                {"source": "d1", "target": "gone", "label": "X"},
                gnode("unrelated", "ip", "0.0.0.0"),
            ),
        ),
    )
    result = await call(session, "get_node", sketch_id=SKETCH, node="d1")

    assert result["node"]["label"] == "evil.example"
    assert [n["label"] for n in result["neighbors"]["RESOLVES_TO (outgoing)"]] == [
        "1.2.3.4",
        "5.6.7.8",
    ]
    assert [n["ref"] for n in result["neighbors"]["HOSTED_ON (incoming)"]] == ["p1"]
    assert "X (outgoing)" not in result["neighbors"]
    assert "omitted_neighbors" not in result


async def test_get_node_caps_neighbors(session, httpx_mock):
    center = gnode("d1", "domain", "a")
    pairs = [
        (
            {"source": "d1", "target": f"i{i}", "label": "R"},
            gnode(f"i{i}", "ip", str(i)),
        )
        for i in range(5)
    ]
    httpx_mock.add_response(json=neighbors(center, *pairs))
    result = await call(
        session, "get_node", sketch_id=SKETCH, node="d1", neighbor_limit=2
    )
    assert len(result["neighbors"]["R (outgoing)"]) == 2
    assert result["omitted_neighbors"] == 3


async def test_get_node_by_type_and_label(session, httpx_mock):
    httpx_mock.add_response(
        url=SEARCH, json=search_page([gnode("d1", "domain", "a.example")])
    )
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/d1",
        json=neighbors(gnode("d1", "domain", "a.example")),
    )
    result = await call(
        session,
        "get_node",
        sketch_id=SKETCH,
        node={"type": "Domain", "label": "a.example"},
    )
    assert result == {
        "node": {"ref": "d1", "type": "domain", "label": "a.example", "props": {}},
        "neighbors": {},
    }


async def test_get_node_unknown_label(session, httpx_mock):
    httpx_mock.add_response(url=SEARCH, json=search_page([]))
    text = await call_error(
        session, "get_node", sketch_id=SKETCH, node={"type": "Domain", "label": "nope"}
    )
    assert "No Domain node labelled 'nope'" in text


async def test_sketch_summary(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/stats",
        json={
            "nodes": 4,
            "edges": 3,
            "by_type": {"domain": 2, "ip": 2},
            "by_relationship": {"RESOLVES_TO": 3},
            "top_nodes": [{"id": "i1", "type": "ip", "label": "1.2.3.4", "degree": 2}],
        },
    )
    result = await call(session, "sketch_summary", sketch_id=SKETCH)
    assert result == {
        "nodes": 4,
        "edges": 3,
        "nodes_by_type": {"domain": 2, "ip": 2},
        "relationships_by_label": {"RESOLVES_TO": 3},
        "most_connected": [
            {"ref": "i1", "type": "ip", "label": "1.2.3.4", "degree": 2}
        ],
    }


async def test_search_investigation(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/investigations/inv-1/nodes/search",
        json={
            "nodes": [
                {
                    "sketch_id": "s1",
                    "sketch_title": "Infra",
                    "node": gnode("i1", "ip", "203.0.113.7", address="203.0.113.7"),
                },
                {
                    "sketch_id": "s2",
                    "sketch_title": "Kits",
                    "node": gnode("i9", "ip", "203.0.113.7"),
                },
            ],
            "next_cursor": "i9",
        },
    )
    result = await call(
        session,
        "search_investigation",
        investigation_id="inv-1",
        types=["Ip"],
        query="203.0.113.7",
        limit=2,
    )
    assert result == {
        "nodes": [
            {
                "sketch": {"id": "s1", "title": "Infra"},
                "ref": "i1",
                "type": "ip",
                "label": "203.0.113.7",
                "props": {"address": "203.0.113.7"},
            },
            {
                "sketch": {"id": "s2", "title": "Kits"},
                "ref": "i9",
                "type": "ip",
                "label": "203.0.113.7",
                "props": {},
            },
        ],
        "next_cursor": "i9",
    }
    assert sent_json(httpx_mock.get_request()) == {
        "types": ["Ip"],
        "query": "203.0.113.7",
        "limit": 2,
    }


async def test_search_investigation_forbidden(session, httpx_mock):
    httpx_mock.add_response(status_code=403, json={"detail": "Forbidden"})
    text = await call_error(session, "search_investigation", investigation_id="x")
    assert "No access" in text
