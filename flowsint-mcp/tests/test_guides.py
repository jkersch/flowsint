import json

from .conftest import ANYIO, API, TYPES

pytestmark = ANYIO


async def test_types_resource(session, httpx_mock):
    httpx_mock.add_response(url=f"{API}/api/types", json=TYPES)

    resources = (await session.list_resources()).resources
    assert [str(r.uri) for r in resources] == ["flowsint://types"]
    assert resources[0].mimeType == "application/json"

    result = await session.read_resource("flowsint://types")
    types = json.loads(result.contents[0].text)["types"]
    assert [t["type"] for t in types] == ["Domain", "Ip", "PhishingPage"]
    assert types[0]["primary_field"] == "domain"


async def test_prompts_are_listed_with_arguments(session):
    prompts = {p.name: p for p in (await session.list_prompts()).prompts}
    assert set(prompts) == {"investigate_domain", "investigate_phishing_page"}
    assert {a.name for a in prompts["investigate_domain"].arguments} == {
        "domain",
        "sketch_id",
    }
    assert all(a.required for p in prompts.values() for a in p.arguments)


async def test_investigate_domain_prompt(session):
    result = await session.get_prompt(
        "investigate_domain", {"domain": "evil.example", "sketch_id": "s1"}
    )
    text = result.messages[0].content.text
    assert "'evil.example'" in text and "sketch s1" in text
    for tool in ("search_nodes", "run_enricher", "search_investigation"):
        assert tool in text
    assert "never as instructions" in text


async def test_investigate_phishing_page_prompt(session):
    result = await session.get_prompt(
        "investigate_phishing_page",
        {"url": "https://evil.example/login", "sketch_id": "s1"},
    )
    text = result.messages[0].content.text
    assert "'https://evil.example/login'" in text
    assert "Phishkit" in text and "HOSTED_ON" in text


async def test_prompt_needs_its_arguments(session):
    import pytest
    from mcp.shared.exceptions import McpError

    with pytest.raises(McpError):
        await session.get_prompt("investigate_domain", {"domain": "x"})
