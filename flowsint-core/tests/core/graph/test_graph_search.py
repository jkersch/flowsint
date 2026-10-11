"""Tests for node search and sketch statistics.

The Neo4j repository is tested on the Cypher and parameters it sends; the
in-memory repository (used by service tests) on its results, so both
implementations of the protocol are pinned to the same contract.
"""

from unittest.mock import MagicMock

import pytest

from flowsint_core.core.graph import GraphSerializer, GraphService, Neo4jGraphRepository
from flowsint_types import Domain, Ip, PhishingPage

from .in_memory_graph_repository import InMemoryGraphRepository

SKETCH = "sketch-1"


def neo4j_repo(*query_results):
    connection = MagicMock()
    connection.query.side_effect = list(query_results)
    return Neo4jGraphRepository(neo4j_connection=connection), connection


def repo_without_connection():
    repo = Neo4jGraphRepository(neo4j_connection=MagicMock())
    repo._connection = None
    return repo


# --- Neo4jGraphRepository.search_nodes ---------------------------------------


class TestNeo4jSearchNodes:
    def test_minimal_search_sends_sketch_and_null_filters(self):
        repo, conn = neo4j_repo([])
        assert repo.search_nodes(SKETCH) == {"nodes": [], "next_cursor": None}

        cypher, params = conn.query.call_args.args
        assert "MATCH (n)\n" in cypher
        assert "n.sketch_id = $sketch_id" in cypher
        assert params == {
            "sketch_id": SKETCH,
            "sketch_ids": None,
            "query": None,
            "filters": {},
            "cursor": None,
            "limit": 51,
        }

    def test_types_become_a_label_expression(self):
        repo, conn = neo4j_repo([])
        repo.search_nodes(SKETCH, types=["domain", "phishingpage"])
        cypher = conn.query.call_args.args[0]
        assert "MATCH (n:`domain`|`phishingpage`)" in cypher

    def test_filters_target_node_properties(self):
        repo, conn = neo4j_repo([])
        repo.search_nodes(SKETCH, filters={"status": "VERIFIED", "domain.domain": "x"})
        assert conn.query.call_args.args[1]["filters"] == {
            "nodeProperties.status": "VERIFIED",
            "nodeProperties.domain.domain": "x",
        }

    def test_query_and_cursor_are_parameters(self):
        repo, conn = neo4j_repo([])
        repo.search_nodes(SKETCH, query="RBFCU", cursor="4:abc:7", limit=10)
        params = conn.query.call_args.args[1]
        assert (params["query"], params["cursor"], params["limit"]) == (
            "RBFCU",
            "4:abc:7",
            11,
        )

    def test_full_page_returns_cursor_of_last_returned_node(self):
        rows = [{"id": f"n{i}", "data": {}} for i in range(3)]
        repo, _ = neo4j_repo(rows)
        result = repo.search_nodes(SKETCH, limit=2)
        assert [n["id"] for n in result["nodes"]] == ["n0", "n1"]
        assert result["next_cursor"] == "n1"

    def test_last_page_has_no_cursor(self):
        rows = [{"id": "n0", "data": {}}, {"id": "n1", "data": {}}]
        repo, _ = neo4j_repo(rows)
        result = repo.search_nodes(SKETCH, limit=2)
        assert len(result["nodes"]) == 2
        assert result["next_cursor"] is None

    def test_none_result_is_empty(self):
        repo, _ = neo4j_repo(None)
        assert repo.search_nodes(SKETCH) == {"nodes": [], "next_cursor": None}

    @pytest.mark.parametrize(
        "bad_type", ["Domain", "domain`) DETACH DELETE n //", "1abc", "", "a-b"]
    )
    def test_unsafe_types_are_rejected_before_querying(self, bad_type):
        repo, conn = neo4j_repo([])
        with pytest.raises(ValueError, match="Invalid node type"):
            repo.search_nodes(SKETCH, types=[bad_type])
        conn.query.assert_not_called()

    @pytest.mark.parametrize("bad_field", ["a b", "a..b", ".a", "a]", "x' OR 1=1"])
    def test_unsafe_filter_fields_are_rejected(self, bad_field):
        repo, conn = neo4j_repo([])
        with pytest.raises(ValueError, match="Invalid filter field"):
            repo.search_nodes(SKETCH, filters={bad_field: "x"})
        conn.query.assert_not_called()

    def test_several_sketches(self):
        repo, conn = neo4j_repo([])
        repo.search_nodes(SKETCH, sketch_ids=["s1", "s2"])
        cypher, params = conn.query.call_args.args
        assert "n.sketch_id IN $sketch_ids" in cypher
        assert "n.sketch_id = $sketch_id" not in cypher
        assert params["sketch_ids"] == ["s1", "s2"]

    def test_no_connection(self):
        assert repo_without_connection().search_nodes(SKETCH) == {
            "nodes": [],
            "next_cursor": None,
        }


# --- Neo4jGraphRepository.get_sketch_stats -----------------------------------


class TestNeo4jSketchStats:
    def test_aggregates_query_rows(self):
        repo, conn = neo4j_repo(
            [{"type": "domain", "count": 3}, {"type": "ip", "count": 2}],
            [{"label": "RESOLVES_TO", "count": 4}],
            [{"id": "n1", "type": "domain", "label": "a.example", "degree": 3}],
        )
        assert repo.get_sketch_stats(SKETCH, top_n=5) == {
            "nodes": 5,
            "edges": 4,
            "by_type": {"domain": 3, "ip": 2},
            "by_relationship": {"RESOLVES_TO": 4},
            "top_nodes": [
                {"id": "n1", "type": "domain", "label": "a.example", "degree": 3}
            ],
        }
        for call in conn.query.call_args_list:
            assert call.args[1] == {"sketch_id": SKETCH, "top_n": 5}

    def test_nodes_without_type_are_counted_as_unknown(self):
        repo, _ = neo4j_repo(
            [{"type": None, "count": 2}, {"type": "", "count": 1}], [], []
        )
        assert repo.get_sketch_stats(SKETCH)["by_type"] == {"unknown": 3}

    def test_empty_sketch(self):
        repo, _ = neo4j_repo(None, None, None)
        assert repo.get_sketch_stats(SKETCH) == {
            "nodes": 0,
            "edges": 0,
            "by_type": {},
            "by_relationship": {},
            "top_nodes": [],
        }

    def test_no_connection(self):
        assert repo_without_connection().get_sketch_stats(SKETCH)["nodes"] == 0


# --- InMemoryGraphRepository + GraphService ----------------------------------


@pytest.fixture
def populated():
    """Two domains, an IP and a phishing page; one domain soft-deleted later."""
    repo = InMemoryGraphRepository()
    entities = [
        Domain(domain="rbfcu-login.example"),
        Domain(domain="other.example"),
        Ip(address="203.0.113.7"),
        PhishingPage(url="https://rbfcu-login.example/x", status="VERIFIED"),
        PhishingPage(url="https://other.example/y", status="PENDING"),
    ]
    result = repo.batch_create_nodes(
        GraphSerializer.serialize_flowsint_types(entities), SKETCH
    )
    ids = dict(zip(["d1", "d2", "ip", "p1", "p2"], result["node_ids"]))
    for src, dst, label in [
        ("p1", "d1", "HOSTED_ON"),
        ("p2", "d2", "HOSTED_ON"),
        ("d1", "ip", "RESOLVES_TO"),
        ("d2", "ip", "RESOLVES_TO"),
    ]:
        repo.create_relationship_by_element_id(ids[src], ids[dst], label, SKETCH)
    # Noise in another sketch must never show up.
    repo.batch_create_nodes(
        GraphSerializer.serialize_flowsint_types([Domain(domain="rbfcu.other")]),
        "sketch-2",
    )
    return repo, ids


def service_for(repo):
    return GraphService(sketch_id=SKETCH, repository=repo)


class TestGraphServiceSearch:
    def test_returns_graph_nodes_with_parsed_properties(self, populated):
        repo, ids = populated
        nodes, cursor = service_for(repo).search_nodes(types=["phishingpage"])

        assert cursor is None
        assert {n.nodeLabel for n in nodes} == {
            "https://rbfcu-login.example/x",
            "https://other.example/y",
        }
        assert all(isinstance(n.nodeProperties, PhishingPage) for n in nodes)

    def test_query_is_case_insensitive_substring(self, populated):
        repo, _ = populated
        nodes, _ = service_for(repo).search_nodes(query="RBFCU")
        assert sorted(n.nodeLabel for n in nodes) == [
            "https://rbfcu-login.example/x",
            "rbfcu-login.example",
        ]

    def test_filters_match_type_fields(self, populated):
        repo, _ = populated
        nodes, _ = service_for(repo).search_nodes(filters={"status": "VERIFIED"})
        assert [n.nodeLabel for n in nodes] == ["https://rbfcu-login.example/x"]

    def test_combined_criteria(self, populated):
        repo, _ = populated
        nodes, _ = service_for(repo).search_nodes(
            types=["domain", "ip"], query="example"
        )
        assert sorted(n.nodeLabel for n in nodes) == [
            "other.example",
            "rbfcu-login.example",
        ]

    def test_paging_visits_every_node_once(self, populated):
        repo, _ = populated
        service = service_for(repo)
        seen, cursor = [], None
        while True:
            nodes, cursor = service.search_nodes(limit=2, cursor=cursor)
            seen += [n.id for n in nodes]
            if cursor is None:
                break
        assert len(seen) == 5 == len(set(seen))

    def test_soft_deleted_nodes_are_excluded(self, populated):
        repo, ids = populated
        repo.delete_nodes([ids["d1"]], SKETCH)
        nodes, _ = service_for(repo).search_nodes(types=["domain"])
        assert [n.nodeLabel for n in nodes] == ["other.example"]

    def test_passes_arguments_to_repository(self):
        repo = MagicMock()
        repo.search_nodes.return_value = {"nodes": [], "next_cursor": "c2"}
        nodes, cursor = service_for(repo).search_nodes(
            types=["ip"], query="q", filters={"a": 1}, limit=5, cursor="c1"
        )
        assert (nodes, cursor) == ([], "c2")
        repo.search_nodes.assert_called_once_with(
            sketch_id=SKETCH,
            types=["ip"],
            query="q",
            filters={"a": 1},
            limit=5,
            cursor="c1",
        )


class TestGraphServiceStats:
    def test_counts_and_top_nodes(self, populated):
        repo, ids = populated
        stats = service_for(repo).get_stats(top_n=2)

        assert stats["nodes"] == 5
        assert stats["edges"] == 4
        assert stats["by_type"] == {"domain": 2, "ip": 1, "phishingpage": 2}
        assert stats["by_relationship"] == {"HOSTED_ON": 2, "RESOLVES_TO": 2}
        # The two domains and the IP all have degree 2; ties are ordered by id.
        tied = sorted([ids["d1"], ids["d2"], ids["ip"]])
        assert [n["id"] for n in stats["top_nodes"]] == tied[:2]
        assert all(n["degree"] == 2 for n in stats["top_nodes"])

    def test_top_nodes_ranked_by_degree(self, populated):
        repo, ids = populated
        repo.create_relationship_by_element_id(ids["p1"], ids["ip"], "SEEN_ON", SKETCH)
        top = service_for(repo).get_stats(top_n=1)["top_nodes"]
        assert top == [
            {"id": ids["ip"], "type": "ip", "label": "203.0.113.7", "degree": 3}
        ]

    def test_edges_to_deleted_nodes_are_not_counted(self, populated):
        repo, ids = populated
        repo.delete_nodes([ids["ip"]], SKETCH)
        stats = service_for(repo).get_stats()
        assert stats["by_relationship"] == {"HOSTED_ON": 2}
        assert "ip" not in stats["by_type"]

    def test_passes_sketch_to_repository(self):
        repo = MagicMock()
        repo.get_sketch_stats.return_value = {"nodes": 0}
        assert service_for(repo).get_stats(top_n=3) == {"nodes": 0}
        repo.get_sketch_stats.assert_called_once_with(SKETCH, top_n=3)


class TestGraphServiceSearchAcross:
    def test_finds_nodes_in_every_listed_sketch(self, populated):
        repo, _ = populated
        hits, cursor = service_for(repo).search_nodes_across(
            sketch_ids=[SKETCH, "sketch-2"], query="rbfcu", types=["domain"]
        )
        assert cursor is None
        assert sorted((sid, n.nodeLabel) for sid, n in hits) == [
            ("sketch-1", "rbfcu-login.example"),
            ("sketch-2", "rbfcu.other"),
        ]

    def test_unlisted_sketches_are_not_searched(self, populated):
        repo, _ = populated
        hits, _ = service_for(repo).search_nodes_across(
            sketch_ids=["sketch-2"], types=["domain"]
        )
        assert [n.nodeLabel for _, n in hits] == ["rbfcu.other"]

    def test_paging(self, populated):
        repo, _ = populated
        service = service_for(repo)
        first, cursor = service.search_nodes_across(
            sketch_ids=[SKETCH, "sketch-2"], limit=4
        )
        rest, end = service.search_nodes_across(
            sketch_ids=[SKETCH, "sketch-2"], limit=4, cursor=cursor
        )
        assert len(first) == 4 and len(rest) == 2 and end is None

    def test_passes_sketch_ids_to_repository(self):
        repo = MagicMock()
        repo.search_nodes.return_value = {"nodes": [], "next_cursor": None}
        service_for(repo).search_nodes_across(sketch_ids=["a", "b"], query="q")
        assert repo.search_nodes.call_args.kwargs["sketch_ids"] == ["a", "b"]
