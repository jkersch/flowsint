from .conftest import ANYIO, API, SKETCH, TYPES, call

pytestmark = ANYIO


async def test_list_investigations(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/investigations",
        json=[
            {
                "id": "inv-1",
                "name": "Phishing RBFCU",
                "description": "Campaign\nlong notes",
                "status": "active",
                "current_user_role": "owner",
                "last_updated_at": "2026-10-01T00:00:00",
                "owner": {"email": "x"},
                "analyses": [{"id": "a"}],
                "sketches": [{"id": SKETCH, "title": "Infra", "description": "d"}],
            }
        ],
    )
    result = await call(session, "list_investigations")
    assert result == {
        "investigations": [
            {
                "id": "inv-1",
                "name": "Phishing RBFCU",
                "description": "Campaign",
                "status": "active",
                "your_role": "owner",
                "last_updated_at": "2026-10-01T00:00:00",
                "sketches": [{"id": SKETCH, "title": "Infra"}],
            }
        ]
    }


async def test_list_sketches(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/investigations/inv-1/sketches",
        json=[
            {"id": SKETCH, "title": "Infra", "description": None, "status": "active"}
        ],
    )
    result = await call(session, "list_sketches", investigation_id="inv-1")
    assert result["sketches"][0]["title"] == "Infra"
    assert result["sketches"][0]["description"] == ""


async def test_get_sketch_includes_counts(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}",
        json={"id": SKETCH, "title": "Infra", "investigation_id": "inv-1"},
    )
    httpx_mock.add_response(
        url=f"{API}/api/sketches/{SKETCH}/stats",
        json={"nodes": 3, "edges": 1, "by_type": {"domain": 3}},
    )
    result = await call(session, "get_sketch", sketch_id=SKETCH)
    assert result["sketch"]["investigation_id"] == "inv-1"
    assert result["nodes"] == 3
    assert result["nodes_by_type"] == {"domain": 3}


async def test_list_types_merges_categories(session, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES)
    result = await call(session, "list_types")
    domain = next(t for t in result["types"] if t["type"] == "Domain")
    assert domain == {
        "type": "Domain",
        "description": "Represents a domain name.",
        "primary_field": "domain",
        "fields": ["domain (required)", "root"],
        "categories": ["Network", "Identities & Entities"],
    }
    assert [t["type"] for t in result["types"]] == ["Domain", "Ip", "PhishingPage"]


async def test_list_types_by_category_label_or_key(session, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES, is_reusable=True)
    by_label = await call(session, "list_types", category="threat intelligence")
    by_key = await call(session, "list_types", category="THREAT_INTEL")
    assert [t["type"] for t in by_label["types"]] == ["PhishingPage"]
    assert by_key == by_label


ENRICHERS = [
    {
        "name": "domain_to_ip",
        "description": "[DNS] Resolve a domain.\nMore.",
        "inputs": {"type": "Domain", "properties": []},
        "outputs": {"type": "Ip", "properties": []},
        "params_schema": [],
    },
    {
        "name": "ip_to_asn",
        "description": None,
        "inputs": {"type": "Ip"},
        "outputs": {"type": "ASN"},
        "params_schema": [
            {
                "name": "API_KEY",
                "type": "vaultSecret",
                "required": True,
                "description": "Key",
            }
        ],
    },
    {"name": "template-x", "description": "Template", "params_schema": None},
]


async def test_list_enrichers(session, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/enrichers", json=ENRICHERS)
    result = await call(session, "list_enrichers")
    assert result["enrichers"][0] == {
        "name": "domain_to_ip",
        "description": "[DNS] Resolve a domain.",
        "input_type": "Domain",
        "output_type": "Ip",
        "params": [],
    }
    assert result["enrichers"][1]["params"] == [
        {
            "name": "API_KEY",
            "type": "vaultSecret",
            "required": True,
            "description": "Key",
        }
    ]
    assert result["enrichers"][2]["input_type"] is None


async def test_list_enrichers_by_input_type(session, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/enrichers", json=ENRICHERS)
    result = await call(session, "list_enrichers", input_type="ip")
    assert [e["name"] for e in result["enrichers"]] == ["ip_to_asn"]


async def test_list_flows(session, httpx_mock):
    httpx_mock.add_response(
        url=f"{API}/api/flows",
        json=[
            {
                "id": "f1",
                "name": "Domain recon",
                "description": "x",
                "category": ["Domain"],
            },
            {"id": "f2", "name": "Empty", "description": None, "category": None},
        ],
    )
    result = await call(session, "list_flows")
    assert result["flows"] == [
        {
            "id": "f1",
            "name": "Domain recon",
            "description": "x",
            "input_types": ["Domain"],
        },
        {"id": "f2", "name": "Empty", "description": "", "input_types": []},
    ]
