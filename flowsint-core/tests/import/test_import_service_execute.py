"""Tests for ImportService.execute_import, including the written-node report."""

from unittest.mock import MagicMock

from tests.core.graph.in_memory_graph_repository import InMemoryGraphRepository

from flowsint_core.core.graph import GraphService
from flowsint_core.imports.import_service import EntityMapping, ImportService

SHA = "7b4791d7a3ad845d36e7d927abc32d0d2a85f67e380213197db000e7c6cb2509"


def mapping(ref, entity_type, data, include=True):
    return EntityMapping(
        id=ref,
        entity_type=entity_type,
        nodeLabel=ref,
        data=data,
        include=include,
        node_id=ref,
    )


def service():
    repo = InMemoryGraphRepository()
    return ImportService(GraphService(sketch_id="s1", repository=repo)), repo


def test_reports_written_nodes_with_computed_labels():
    import_service, repo = service()
    result = import_service.execute_import(
        [
            mapping("kit", "Phishkit", {"sha256": SHA}),
            mapping("dom", "Domain", {"domain": "evil.example"}),
        ]
    )

    assert result.status == "completed"
    assert result.nodes_created == 2
    assert [(n["ref"], n["type"], n["label"]) for n in result.nodes] == [
        ("kit", "phishkit", f"kit:{SHA[:12]}"),
        ("dom", "domain", "evil.example"),
    ]
    for node in result.nodes:
        assert repo._nodes[node["id"]]["nodeLabel"] == node["label"]


def test_invalid_entities_are_reported_and_left_out_of_nodes():
    import_service, _ = service()
    result = import_service.execute_import(
        [
            mapping("bad", "Phishkit", {"sha256": "nope"}),
            mapping("ok", "Domain", {"domain": "a.example"}),
            mapping("unknown", "NoSuchType", {"x": 1}),
        ]
    )

    assert result.status == "completed_with_errors"
    assert result.nodes_created == 1
    assert [n["ref"] for n in result.nodes] == ["ok"]
    assert len(result.errors) == 2
    assert any("Unknown type" in e for e in result.errors)


def test_excluded_entities_are_skipped():
    import_service, _ = service()
    result = import_service.execute_import(
        [
            mapping("a", "Domain", {"domain": "a.example"}),
            mapping("b", "Domain", {"domain": "b.example"}, include=False),
        ]
    )
    assert [n["ref"] for n in result.nodes] == ["a"]
    assert result.nodes_skipped == 0


def test_edges_link_written_nodes():
    import_service, repo = service()
    result = import_service.execute_import(
        [
            mapping("d", "Domain", {"domain": "a.example"}),
            mapping("i", "Ip", {"address": "203.0.113.7"}),
        ],
        edges=[
            {"from_id": "d", "to_id": "i", "label": "RESOLVES_TO"},
            {"from_id": "d", "to_id": "ghost", "label": "X"},
        ],
    )
    ids = {n["ref"]: n["id"] for n in result.nodes}
    edges = list(repo._edges.values())
    assert [(e["source"], e["target"], e["type"]) for e in edges] == [
        (ids["d"], ids["i"], "RESOLVES_TO")
    ]
    assert any("Missing source or target" in e for e in result.errors)


def test_nothing_valid_to_import():
    import_service, _ = service()
    result = import_service.execute_import([mapping("x", "NoSuchType", {})])
    assert result.nodes_created == 0
    assert result.nodes == []
    assert result.status == "completed_with_errors"


def test_batch_errors_drop_the_node_report():
    """Element ids no longer line up with entities, so none are reported."""
    graph = MagicMock()
    graph._type_resolver = None
    graph.batch_create_nodes.return_value = {
        "nodes_created": 1,
        "node_ids": ["e1"],
        "errors": ["Node 1: boom"],
    }
    result = ImportService(graph).execute_import(
        [
            mapping("a", "Domain", {"domain": "a.example"}),
            mapping("b", "Domain", {"domain": "b.example"}),
        ]
    )
    assert result.nodes == []
    assert result.errors == ["Node 1: boom"]


def test_batch_failure_marks_import_failed():
    graph = MagicMock()
    graph._type_resolver = None
    graph.batch_create_nodes.side_effect = RuntimeError("neo4j down")
    result = ImportService(graph).execute_import(
        [mapping("a", "Domain", {"domain": "a.example"})]
    )
    assert result.status == "failed"
    assert result.nodes == []
    assert "neo4j down" in result.errors[0]
