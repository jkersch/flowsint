import json
from urllib.parse import parse_qs

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from flowsint_mcp.tools.graph_write import relationship_label

from .conftest import (
    ANYIO,
    API,
    SKETCH,
    TYPES,
    call,
    call_error,
    gnode,
    search_page,
    sent_json,
)

pytestmark = ANYIO

IMPORT = f"{API}/api/sketches/{SKETCH}/import/execute"
SHA = "7b4791d7a3ad845d36e7d927abc32d0d2a85f67e380213197db000e7c6cb2509"


def import_payload(request):
    form = parse_qs(request.content.decode())
    return json.loads(form["entity_mappings_json"][0])


@pytest.mark.parametrize(
    "label, expected",
    [
        ("hosted on", "HOSTED_ON"),
        ("RESOLVES_TO", "RESOLVES_TO"),
        ("  uses-kit! ", "USES_KIT"),
        ("ip2asn", "IP2ASN"),
    ],
)
def test_relationship_label(label, expected):
    assert relationship_label(label) == expected


def test_relationship_label_needs_content():
    with pytest.raises(ToolError, match="at least one letter"):
        relationship_label(" -- ")


async def test_add_node_uses_validated_import(session, httpx_mock):
    httpx_mock.add_response(
        url=IMPORT,
        json={
            "status": "completed",
            "nodes_created": 1,
            "nodes": [
                {
                    "ref": "node",
                    "id": "4:x:9",
                    "type": "phishkit",
                    "label": f"kit:{SHA[:12]}",
                }
            ],
            "errors": [],
        },
    )
    result = await call(
        session, "add_node", sketch_id=SKETCH, type="Phishkit", data={"sha256": SHA}
    )
    assert result == {
        "node": {"ref": "4:x:9", "type": "phishkit", "label": f"kit:{SHA[:12]}"}
    }
    assert import_payload(httpx_mock.get_request()) == {
        "nodes": [
            {
                "id": "node",
                "node_id": "node",
                "entity_type": "Phishkit",
                "nodeLabel": "node",
                "include": True,
                "data": {"sha256": SHA},
            }
        ],
        "edges": [],
    }


async def test_add_node_reports_validation_errors(session, httpx_mock):
    httpx_mock.add_response(
        url=IMPORT,
        json={
            "status": "completed_with_errors",
            "nodes": [],
            "errors": ["Entity 1 (node): Invalid SHA-256"],
        },
    )
    text = await call_error(
        session, "add_node", sketch_id=SKETCH, type="Phishkit", data={"sha256": "x"}
    )
    assert "Could not add the Phishkit node: Entity 1 (node): Invalid SHA-256" in text


async def test_add_node_without_error_details(session, httpx_mock):
    httpx_mock.add_response(url=IMPORT, json={"status": "failed"})
    text = await call_error(session, "add_node", sketch_id=SKETCH, type="X", data={})
    assert "unknown error" in text


async def test_add_relationship_resolves_refs_and_normalizes_label(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/search",
        json=search_page([gnode("d1", "domain", "evil.example")]),
    )
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/relations/add", json={"status": "edge added"}
    )
    result = await call(
        session,
        "add_relationship",
        sketch_id=SKETCH,
        source="p1",
        target={"type": "Domain", "label": "evil.example"},
        label="hosted on",
    )
    assert result == {"source": "p1", "target": "d1", "label": "HOSTED_ON"}
    assert sent_json(httpx_mock.get_requests()[-1]) == {
        "source": "p1",
        "target": "d1",
        "type": "one-way",
        "label": "HOSTED_ON",
    }


async def test_add_relationship_rejects_empty_label_before_calling_api(
    session, httpx_mock
):
    text = await call_error(
        session,
        "add_relationship",
        sketch_id=SKETCH,
        source="a",
        target="b",
        label="!!",
    )
    assert "at least one letter" in text
    assert httpx_mock.get_requests() == []


def neighbors_of(node):
    return {"nds": [node], "rls": []}


async def test_edit_node_sets_type_fields(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/p1",
        json=neighbors_of(gnode("p1", "phishingpage", "https://evil/")),
    )
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES)
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/nodes/edit", json={"status": "node updated"}
    )
    result = await call(
        session,
        "edit_node",
        sketch_id=SKETCH,
        node="p1",
        updates={"status": "NEUTRALIZED", "title": "Login"},
    )
    assert result == {"node": "p1", "updated": ["status", "title"]}
    assert sent_json(httpx_mock.get_requests()[-1]) == {
        "nodeId": "p1",
        "updates": {"nodeProperties": {"status": "NEUTRALIZED", "title": "Login"}},
    }


async def test_edit_node_refuses_primary_field(session, httpx_mock):
    httpx_mock.add_response(
        json=neighbors_of(gnode("p1", "phishingpage", "https://evil/"))
    )
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES)
    text = await call_error(
        session, "edit_node", sketch_id=SKETCH, node="p1", updates={"url": "https://x/"}
    )
    assert "'url' identifies this phishingpage" in text
    assert not any(
        r.url.path.endswith("/nodes/edit") for r in httpx_mock.get_requests()
    )


async def test_edit_node_of_type_unknown_to_picker(session, httpx_mock):
    httpx_mock.add_response(json=neighbors_of(gnode("c1", "customthing", "c")))
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES)
    httpx_mock.add_response(url=f"{API}/api/sketches/{SKETCH}/nodes/edit", json={})
    result = await call(
        session, "edit_node", sketch_id=SKETCH, node="c1", updates={"a": 1}
    )
    assert result["updated"] == ["a"]


async def test_edit_node_needs_updates(session, httpx_mock):
    text = await call_error(
        session, "edit_node", sketch_id=SKETCH, node="p1", updates={}
    )
    assert "Nothing to update" in text


async def test_import_entities(session, httpx_mock):
    httpx_mock.add_response(
        url=IMPORT,
        json={
            "status": "completed_with_errors",
            "nodes_created": 2,
            "nodes": [
                {
                    "ref": "p",
                    "id": "4:x:1",
                    "type": "phishingpage",
                    "label": "https://evil/",
                },
                {"ref": "d", "id": "4:x:2", "type": "domain", "label": "evil"},
            ],
            "errors": ["Entity 3 (bad): invalid"],
        },
    )
    result = await call(
        session,
        "import_entities",
        sketch_id=SKETCH,
        nodes=[
            {"ref": "p", "type": "PhishingPage", "data": {"url": "https://evil/"}},
            {"ref": "d", "type": "Domain", "data": {"domain": "evil"}},
            {"ref": "bad", "type": "Domain", "data": {}},
        ],
        edges=[{"source": "p", "target": "d", "label": "hosted on"}],
    )
    assert result == {
        "status": "completed_with_errors",
        "nodes_written": 2,
        "nodes": {
            "items": [
                {"ref": "p", "node": "4:x:1", "label": "https://evil/"},
                {"ref": "d", "node": "4:x:2", "label": "evil"},
            ]
        },
        "errors": ["Entity 3 (bad): invalid"],
    }
    payload = import_payload(httpx_mock.get_request())
    assert [n["entity_type"] for n in payload["nodes"]] == [
        "PhishingPage",
        "Domain",
        "Domain",
    ]
    assert payload["edges"] == [{"from_id": "p", "to_id": "d", "label": "HOSTED_ON"}]


async def test_import_reports_at_most_100_nodes(session, httpx_mock):
    written = [
        {"ref": f"n{i}", "id": f"4:x:{i}", "type": "domain", "label": f"d{i}"}
        for i in range(150)
    ]
    httpx_mock.add_response(
        url=IMPORT, json={"status": "completed", "nodes_created": 150, "nodes": written}
    )
    result = await call(
        session,
        "import_entities",
        sketch_id=SKETCH,
        nodes=[
            {"ref": f"n{i}", "type": "Domain", "data": {"domain": f"d{i}"}}
            for i in range(150)
        ],
    )
    assert len(result["nodes"]["items"]) == 100
    assert result["nodes"]["omitted"] == 50
    assert result["errors"] == []


@pytest.mark.parametrize(
    "nodes, edges, message",
    [
        (
            [
                {"ref": "a", "type": "Domain", "data": {}},
                {"ref": "a", "type": "Ip", "data": {}},
            ],
            [],
            "Duplicate node refs: a",
        ),
        (
            [{"ref": "a", "type": "Domain", "data": {}}],
            [{"source": "a", "target": "zz", "label": "X"}],
            "unknown refs: zz",
        ),
        ([], [], "at least 1"),
    ],
)
async def test_import_validates_before_calling_api(
    session, httpx_mock, nodes, edges, message
):
    text = await call_error(
        session, "import_entities", sketch_id=SKETCH, nodes=nodes, edges=edges
    )
    assert message in text
    assert httpx_mock.get_requests() == []
