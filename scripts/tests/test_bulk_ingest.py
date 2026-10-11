import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import bulk_ingest
import pytest
from bulk_ingest import FlowsintClient, build_payload, plan_chunks, read_records

from flowsint_core.core.services.type_registry_service import local_type_resolver
from flowsint_core.imports.import_service import EntityMapping, ImportService

SHA = "7b4791d7a3ad845d36e7d927abc32d0d2a85f67e380213197db000e7c6cb2509"


def write_jsonl(tmp_path, records, name="data.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in records)
    )
    return str(path)


def node(nid, type_="Domain", **data):
    return {"id": nid, "type": type_, "data": data or {"domain": f"{nid}.example"}}


def edge(src, dst, label=None):
    rec = {"from": src, "to": dst}
    if label:
        rec["label"] = label
    return rec


# --- read_records -----------------------------------------------------------


def test_reads_nodes_and_edges(tmp_path):
    path = write_jsonl(tmp_path, [node("a"), node("b"), edge("a", "b", "LINKS_TO")])
    nodes, edges = read_records([path])

    assert nodes["a"] == {"type": "Domain", "data": {"domain": "a.example"}}
    assert edges == [{"from": "a", "to": "b", "label": "LINKS_TO"}]


def test_edge_label_defaults_to_related_to(tmp_path):
    path = write_jsonl(tmp_path, [node("a"), node("b"), edge("a", "b")])
    _, edges = read_records([path])
    assert edges[0]["label"] == "RELATED_TO"


def test_ids_are_stringified(tmp_path):
    path = write_jsonl(
        tmp_path,
        [{"id": 1, "type": "Domain", "data": {"domain": "x.example"}}, node("b")]
        + [edge(1, "b")],
    )
    nodes, edges = read_records([path])
    assert "1" in nodes
    assert edges[0]["from"] == "1"


def test_blank_lines_are_skipped(tmp_path):
    path = write_jsonl(tmp_path, ["", json.dumps(node("a")), "   ", ""])
    nodes, _ = read_records([path])
    assert list(nodes) == ["a"]


def test_duplicate_node_ids_merge_data_later_wins(tmp_path):
    path = write_jsonl(
        tmp_path,
        [
            node("p", "PhishingPage", url="https://x/", status="PENDING"),
            node("p", "PhishingPage", status="VERIFIED", title="Login"),
        ],
    )
    nodes, _ = read_records([path])
    assert nodes["p"]["data"] == {
        "url": "https://x/",
        "status": "VERIFIED",
        "title": "Login",
    }


def test_records_span_multiple_files(tmp_path):
    a = write_jsonl(tmp_path, [node("a")], "a.jsonl")
    b = write_jsonl(tmp_path, [node("b"), edge("a", "b")], "b.jsonl")
    nodes, edges = read_records([a, b])
    assert set(nodes) == {"a", "b"}
    assert len(edges) == 1


@pytest.mark.parametrize(
    "record, message",
    [
        ({"type": "Domain", "data": {}}, "needs 'id' and 'type'"),
        ({"id": "a", "data": {}}, "needs 'id' and 'type'"),
        ({"id": "a", "type": "Domain"}, "'data' must be an object"),
        ({"id": "a", "type": "Domain", "data": "x"}, "'data' must be an object"),
        ({"from": "a"}, "edge without 'to'"),
    ],
)
def test_invalid_records_report_file_and_line(tmp_path, record, message):
    path = write_jsonl(tmp_path, [node("ok"), record])
    with pytest.raises(ValueError, match=message) as exc:
        read_records([path])
    assert f"{path}:2" in str(exc.value)


def test_edges_to_unknown_nodes_are_rejected(tmp_path):
    path = write_jsonl(tmp_path, [node("a"), edge("a", "ghost")])
    with pytest.raises(ValueError, match="1 edge endpoints have no node record: ghost"):
        read_records([path])


def test_invalid_json_raises(tmp_path):
    path = write_jsonl(tmp_path, ["{not json"])
    with pytest.raises(json.JSONDecodeError):
        read_records([path])


# --- plan_chunks ------------------------------------------------------------


def star_graph(spokes):
    """One hub linked to many spokes, plus two unlinked nodes."""
    nodes = {"hub": node("hub")}
    nodes.update({f"s{i}": node(f"s{i}") for i in range(spokes)})
    nodes["o1"], nodes["o2"] = node("o1"), node("o2")
    edges = [{"from": "hub", "to": f"s{i}", "label": "X"} for i in range(spokes)]
    return nodes, edges


@pytest.mark.parametrize("max_nodes, max_edges", [(2, 1), (3, 2), (5, 100), (50, 3)])
def test_chunks_respect_limits_and_carry_their_endpoints(max_nodes, max_edges):
    nodes, edges = star_graph(10)
    chunks = list(plan_chunks(nodes, edges, max_nodes, max_edges))

    for node_ids, chunk_edges in chunks:
        assert len(node_ids) <= max_nodes
        assert len(chunk_edges) <= max_edges
        assert len(node_ids) == len(set(node_ids))
        for e in chunk_edges:
            assert e["from"] in node_ids and e["to"] in node_ids


def test_every_edge_sent_once_and_every_node_at_least_once():
    nodes, edges = star_graph(10)
    chunks = list(plan_chunks(nodes, edges, 3, 2))

    sent_edges = [e for _, es in chunks for e in es]
    assert sent_edges == edges
    assert {n for ids, _ in chunks for n in ids} == set(nodes)


def test_unlinked_nodes_go_in_trailing_node_only_chunks():
    nodes, edges = star_graph(2)
    chunks = list(plan_chunks(nodes, edges, 10, 10))
    assert chunks[-1] == (["o1", "o2"], [])
    assert all(es for _, es in chunks[:-1])


def test_orphans_are_split_by_max_nodes():
    nodes = {f"n{i}": node(f"n{i}") for i in range(5)}
    chunks = list(plan_chunks(nodes, [], 2, 10))
    assert [ids for ids, _ in chunks] == [["n0", "n1"], ["n2", "n3"], ["n4"]]


def test_self_loop_counts_its_node_once():
    nodes = {"a": node("a")}
    edges = [{"from": "a", "to": "a", "label": "X"}]
    assert list(plan_chunks(nodes, edges, 1, 10)) == [(["a"], edges)]


def test_empty_input_yields_no_chunks():
    assert list(plan_chunks({}, [], 10, 10)) == []


# --- build_payload + server-side contract ------------------------------------


def test_payload_shape_matches_import_endpoint():
    nodes = {"a": node("a"), "b": node("b")}
    payload = build_payload(["a", "b"], [{"from": "a", "to": "b", "label": "L"}], nodes)

    assert payload["nodes"][0] == {
        "id": "a",
        "node_id": "a",
        "entity_type": "Domain",
        "nodeLabel": "a",
        "include": True,
        "data": {"domain": "a.example"},
    }
    assert payload["edges"] == [{"from_id": "a", "to_id": "b", "label": "L"}]


class FakeGraphService:
    """Stands in for Neo4j: hands out element ids in insertion order."""

    _type_resolver = staticmethod(local_type_resolver)

    def __init__(self):
        self.nodes = []
        self.edges = []

    def batch_create_nodes(self, nodes):
        start = len(self.nodes)
        self.nodes.extend(nodes)
        ids = [f"elem-{i}" for i in range(start, len(self.nodes))]
        return {"nodes_created": len(nodes), "node_ids": ids, "errors": []}

    def batch_create_edges_by_element_id(self, edges):
        self.edges.extend(edges)
        return {"edges_created": len(edges), "errors": []}


def run_through_import_service(payload):
    """Feed a payload through the same code path /import/execute uses."""
    graph = FakeGraphService()
    result = ImportService(graph).execute_import(
        entity_mappings=[EntityMapping(**m) for m in payload["nodes"]],
        edges=payload["edges"],
    )
    return result, graph


def test_threat_intel_payload_is_accepted_by_import_service():
    nodes = {
        "page": node("page", "PhishingPage", url="https://evil.example/login"),
        "kit": node("kit", "Phishkit", sha256=SHA, file_name="kit.zip"),
        "actor": node("actor", "ThreatActor", name="Group-X"),
        "reg": node("reg", "Registrar", name="NameCheap, Inc.", iana_id=1068),
        "isp": node("isp", "ISP", name="Acme Hosting"),
        "org": node("org", "Organization", name="RBFCU"),
    }
    edges = [
        {"from": "page", "to": "kit", "label": "USES_KIT"},
        {"from": "kit", "to": "actor", "label": "AUTHORED_BY"},
        {"from": "page", "to": "org", "label": "TARGETS"},
    ]
    payload = build_payload(list(nodes), edges, nodes)

    result, graph = run_through_import_service(payload)

    assert result.errors == []
    assert result.nodes_created == 6
    labels = {n["nodeLabel"] for n in graph.nodes}
    assert {"https://evil.example/login", f"kit:{SHA[:12]}", "Group-X"} <= labels
    assert [
        (e["from_element_id"], e["to_element_id"], e["rel_label"]) for e in graph.edges
    ] == [
        ("elem-0", "elem-1", "USES_KIT"),
        ("elem-1", "elem-2", "AUTHORED_BY"),
        ("elem-0", "elem-5", "TARGETS"),
    ]


def test_every_planned_chunk_wires_all_its_edges():
    nodes, edges = star_graph(7)
    for node_ids, chunk_edges in plan_chunks(nodes, edges, 3, 2):
        result, graph = run_through_import_service(
            build_payload(node_ids, chunk_edges, nodes)
        )
        assert result.errors == []
        assert len(graph.edges) == len(chunk_edges)


def test_invalid_entity_is_reported_not_fatal():
    nodes = {
        "bad": node("bad", "Phishkit", sha256="not-a-hash"),
        "ok": node("ok"),
    }
    result, graph = run_through_import_service(build_payload(["bad", "ok"], [], nodes))
    assert result.nodes_created == 1
    assert len(result.errors) == 1 and "bad" in result.errors[0]


# --- FlowsintClient against a stub API ---------------------------------------


class StubAPI:
    """Minimal HTTP server scripted with a queue of import responses."""

    def __init__(self, import_responses):
        self.import_responses = list(import_responses)
        self.logins = 0
        self.import_calls = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                form = parse_qs(
                    self.rfile.read(int(self.headers["Content-Length"])).decode()
                )
                if self.path == "/api/auth/token":
                    stub.logins += 1
                    if form["password"] != ["secret"]:
                        return self._reply(
                            400, {"detail": "Incorrect email or password"}
                        )
                    return self._reply(200, {"access_token": f"tok{stub.logins}"})
                stub.import_calls.append(
                    (
                        self.path,
                        self.headers["Authorization"],
                        json.loads(form["entity_mappings_json"][0]),
                    )
                )
                code, body = stub.import_responses.pop(0)
                self._reply(code, body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


OK = (
    200,
    {"status": "completed", "nodes_created": 1, "nodes_skipped": 0, "errors": []},
)


@pytest.fixture
def stub_api():
    stubs = []

    def make(*responses):
        stub = StubAPI(responses)
        stubs.append(stub)
        return stub

    yield make
    for stub in stubs:
        stub.close()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(bulk_ingest.time, "sleep", lambda s: None)


def client_for(stub, password="secret"):
    client = FlowsintClient(stub.url, "me@example.com", password, timeout=5)
    client.login()
    return client


def test_login_and_import_send_token_and_payload(stub_api):
    stub = stub_api(OK)
    result = client_for(stub).execute_import("sk-1", {"nodes": [], "edges": []}, 3)

    assert result["status"] == "completed"
    path, auth, payload = stub.import_calls[0]
    assert path == "/api/sketches/sk-1/import/execute"
    assert auth == "Bearer tok1"
    assert payload == {"nodes": [], "edges": []}


def test_login_failure_raises(stub_api):
    stub = stub_api()
    with pytest.raises(Exception):
        client_for(stub, password="wrong")


def test_expired_token_triggers_one_relogin(stub_api):
    stub = stub_api((401, {"detail": "expired"}), OK)
    client_for(stub).execute_import("sk", {}, 3)

    assert stub.logins == 2
    assert [auth for _, auth, _ in stub.import_calls] == ["Bearer tok1", "Bearer tok2"]


def test_server_errors_are_retried(stub_api):
    stub = stub_api((502, {}), (500, {}), OK)
    assert client_for(stub).execute_import("sk", {}, 3)["status"] == "completed"
    assert len(stub.import_calls) == 3


def test_server_errors_give_up_after_retries(stub_api):
    stub = stub_api((500, {"detail": "boom"}), (500, {"detail": "boom"}))
    with pytest.raises(RuntimeError, match="HTTP 500.*boom"):
        client_for(stub).execute_import("sk", {}, 1)
    assert len(stub.import_calls) == 2


@pytest.mark.parametrize("code", [400, 403, 404])
def test_client_errors_are_not_retried(stub_api, code):
    stub = stub_api((code, {"detail": "nope"}))
    with pytest.raises(RuntimeError, match=f"HTTP {code}"):
        client_for(stub).execute_import("sk", {}, 3)
    assert len(stub.import_calls) == 1


def test_unreachable_api_raises_after_retries():
    client = FlowsintClient("http://127.0.0.1:9", "e", "p", timeout=1)
    client.token = "t"
    with pytest.raises(RuntimeError, match="Request failed"):
        client.execute_import("sk", {}, 1)


# --- main --------------------------------------------------------------------


def run_main(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["bulk_ingest.py", *argv])
    return bulk_ingest.main()


def test_dry_run_prints_plan_and_sends_nothing(tmp_path, monkeypatch, capsys):
    path = write_jsonl(tmp_path, [node("a"), node("b"), edge("a", "b"), node("c")])
    code = run_main(
        monkeypatch,
        [
            "--email",
            "e",
            "--sketch-id",
            "s",
            "--api",
            "http://127.0.0.1:9",
            "--dry-run",
            path,
        ],
    )
    assert code == 0
    assert "3 nodes, 1 edges -> 2 requests (3 node writes" in capsys.readouterr().out


def test_missing_password_exits_2(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOWSINT_PASSWORD", raising=False)
    path = write_jsonl(tmp_path, [node("a")])
    assert run_main(monkeypatch, ["--email", "e", "--sketch-id", "s", path]) == 2


def test_full_run_logs_server_errors_and_failed_chunks(tmp_path, monkeypatch, stub_api):
    stub = stub_api(
        (
            200,
            {
                "status": "completed_with_errors",
                "nodes_created": 1,
                "nodes_skipped": 1,
                "errors": ["Entity 2 (x): bad"],
            },
        ),
        (403, {"detail": "Forbidden"}),
    )
    monkeypatch.setenv("FLOWSINT_PASSWORD", "secret")
    data = write_jsonl(tmp_path, [node("a"), node("b"), node("c")])
    errors = tmp_path / "errors.log"

    code = run_main(
        monkeypatch,
        [
            "--email",
            "e",
            "--sketch-id",
            "s",
            "--api",
            stub.url,
            "--max-nodes",
            "2",
            "--errors-file",
            str(errors),
            data,
        ],
    )

    assert code == 1
    log = errors.read_text()
    assert "chunk 1: Entity 2 (x): bad" in log
    assert "chunk 2: HTTP 403" in log
    assert len(stub.import_calls) == 2


def test_full_run_succeeds(tmp_path, monkeypatch, stub_api):
    stub = stub_api(OK)
    monkeypatch.setenv("FLOWSINT_PASSWORD", "secret")
    data = write_jsonl(tmp_path, [node("a"), node("b"), edge("a", "b")])
    code = run_main(
        monkeypatch,
        [
            "--email",
            "e",
            "--sketch-id",
            "s",
            "--api",
            stub.url,
            "--errors-file",
            str(tmp_path / "e.log"),
            data,
        ],
    )
    assert code == 0
    _, _, payload = stub.import_calls[0]
    assert [n["id"] for n in payload["nodes"]] == ["a", "b"]
    assert payload["edges"] == [{"from_id": "a", "to_id": "b", "label": "RELATED_TO"}]
