import pytest

from flowsint_mcp.refs import NodeKey, NodeRefError, resolve

from .conftest import ANYIO, SKETCH, gnode, search_page, sent_json

pytestmark = ANYIO


async def test_ref_string_is_passed_through(client, httpx_mock):
    assert await resolve(client, SKETCH, " 4:x:1 ") == "4:x:1"
    assert httpx_mock.get_requests() == []


async def test_empty_ref_is_rejected(client):
    with pytest.raises(NodeRefError, match="must not be empty"):
        await resolve(client, SKETCH, "  ")


async def test_exact_label_wins_over_substring_matches(client, httpx_mock):
    httpx_mock.add_response(
        json=search_page(
            [
                gnode("1", "domain", "a.example.org"),
                gnode("2", "domain", "a.example"),
                gnode("3", "domain", "sub.a.example"),
            ]
        )
    )
    key = NodeKey(type="Domain", label="a.example")
    assert await resolve(client, SKETCH, key) == "2"
    assert sent_json(httpx_mock.get_request()) == {
        "types": ["Domain"],
        "query": "a.example",
        "limit": 500,
    }


async def test_case_insensitive_fallback(client, httpx_mock):
    httpx_mock.add_response(json=search_page([gnode("7", "domain", "A.Example")]))
    assert (
        await resolve(client, SKETCH, NodeKey(type="Domain", label="a.example")) == "7"
    )


async def test_exact_match_preferred_over_case_variant(client, httpx_mock):
    httpx_mock.add_response(
        json=search_page([gnode("1", "x", "Acme"), gnode("2", "x", "acme")])
    )
    assert await resolve(client, SKETCH, NodeKey(type="X", label="acme")) == "2"


async def test_follows_pages(client, httpx_mock):
    httpx_mock.add_response(json=search_page([gnode("1", "d", "ab")], cursor="c1"))
    httpx_mock.add_response(json=search_page([gnode("2", "d", "a")]))
    assert await resolve(client, SKETCH, NodeKey(type="D", label="a")) == "2"
    assert sent_json(httpx_mock.get_requests()[1])["cursor"] == "c1"


async def test_stops_after_max_pages(client, httpx_mock):
    for i in range(5):
        httpx_mock.add_response(
            json=search_page([gnode(str(i), "d", "ab")], cursor=f"c{i}")
        )
    with pytest.raises(NodeRefError, match="No D node"):
        await resolve(client, SKETCH, NodeKey(type="D", label="a"))
    assert len(httpx_mock.get_requests()) == 5


async def test_no_match(client, httpx_mock):
    httpx_mock.add_response(json=search_page([]))
    with pytest.raises(NodeRefError, match="No Domain node labelled 'x'"):
        await resolve(client, SKETCH, NodeKey(type="Domain", label="x"))


async def test_ambiguous_match_lists_refs(client, httpx_mock):
    httpx_mock.add_response(
        json=search_page([gnode("1", "d", "dup"), gnode("2", "d", "dup")])
    )
    with pytest.raises(NodeRefError, match="2 D nodes .* refs instead: 1, 2"):
        await resolve(client, SKETCH, NodeKey(type="D", label="dup"))
