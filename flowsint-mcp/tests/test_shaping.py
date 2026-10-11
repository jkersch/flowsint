import json

from flowsint_mcp.shaping import (
    MAX_LIST,
    MAX_STRING,
    compact_list,
    compact_node,
    first_line,
    truncate,
)

from .conftest import gnode


def test_short_values_are_untouched():
    value = {"a": "x", "b": [1, 2], "c": 3, "d": None, "e": True}
    assert truncate(value) == value


def test_long_strings_are_cut_with_marker():
    out = truncate("x" * (MAX_STRING + 50))
    assert out.startswith("x" * MAX_STRING)
    assert out.endswith("… [+50 chars]")


def test_long_lists_are_cut_with_marker():
    out = truncate(list(range(MAX_LIST + 5)))
    assert out[:MAX_LIST] == list(range(MAX_LIST))
    assert out[-1] == "… [+5 more]"


def test_deep_nesting_is_collapsed():
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    assert truncate(deep) == {"a": {"b": {"c": {"d": "{…}"}}}}


def test_compact_node_drops_layout_and_empty_fields():
    node = gnode("4:x:1", "domain", "a.example", domain="a.example", root=None, tags=[])
    assert compact_node(node) == {
        "ref": "4:x:1",
        "type": "domain",
        "label": "a.example",
        "props": {"domain": "a.example"},
    }


def test_compact_node_without_properties():
    assert (
        compact_node({"id": "1", "nodeType": "ip", "nodeLabel": "1.2.3.4"})["props"]
        == {}
    )
    assert compact_node({"id": "1", "nodeProperties": "odd"})["props"] == {}


def test_output_stays_bounded_for_hostile_nodes():
    huge = gnode(
        "1",
        "phishingpage",
        "https://x/",
        content="A" * 100_000,
        links=["https://y/"] * 5_000,
    )
    assert len(json.dumps(compact_node(huge))) < 3_000


def test_first_line():
    assert first_line("\n  Resolve a domain.  \nDetails") == "Resolve a domain."
    assert first_line(None) == ""
    assert first_line("   \n ") == ""


def test_compact_list():
    assert compact_list([1, 2, 3], 5) == {"items": [1, 2, 3]}
    assert compact_list([1, 2, 3], 2) == {"items": [1, 2], "omitted": 1}
