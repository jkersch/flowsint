#!/usr/bin/env python3
"""Bulk-load nodes and edges into a Flowsint sketch through the import API.

Input is JSON Lines, one record per line, in one or more files:

    {"id": "d1", "type": "Domain", "data": {"domain": "evil.example"}}
    {"id": "p1", "type": "PhishingPage", "data": {"url": "https://evil.example/login"}}
    {"from": "p1", "to": "d1", "label": "HOSTED_ON"}

Node records need a unique local ``id`` (only used to wire edges in this run),
a Flowsint ``type`` name and the type's fields in ``data``. Edge records name
two node ids and an optional ``label`` (default RELATED_TO).

Each request to /import/execute can only link nodes sent in the same request,
so edges are chunked and every chunk also carries the nodes it touches. Nodes
are merged on (type, nodeLabel, sketch), so re-sending a node, or re-running
the whole script, updates it instead of duplicating it.

Usage:
    FLOWSINT_PASSWORD=... python3 scripts/bulk_ingest.py \\
        --api http://localhost:5001 --email me@example.com \\
        --sketch-id <uuid> data/*.jsonl
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterator, List, Tuple

Node = Dict[str, object]
Edge = Dict[str, str]


class FlowsintClient:
    def __init__(self, api: str, email: str, password: str, timeout: int):
        self.api = api.rstrip("/")
        self.email = email
        self.password = password
        self.timeout = timeout
        self.token = None

    def login(self) -> None:
        body = urllib.parse.urlencode(
            {"username": self.email, "password": self.password}
        ).encode()
        req = urllib.request.Request(f"{self.api}/api/auth/token", data=body)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            self.token = json.load(resp)["access_token"]

    def execute_import(self, sketch_id: str, payload: dict, retries: int) -> dict:
        body = urllib.parse.urlencode(
            {"entity_mappings_json": json.dumps(payload)}
        ).encode()
        url = f"{self.api}/api/sketches/{sketch_id}/import/execute"
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                url, data=body, headers={"Authorization": f"Bearer {self.token}"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt < retries:
                    self.login()
                    continue
                if e.code < 500 or attempt == retries:
                    raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:500]}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == retries:
                    raise RuntimeError(f"Request failed: {e}")
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")  # pragma: no cover


def read_records(paths: List[str]) -> Tuple[Dict[str, Node], List[Edge]]:
    nodes: Dict[str, Node] = {}
    edges: List[Edge] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                where = f"{path}:{lineno}"
                rec = json.loads(line)
                if "from" in rec:
                    if not rec.get("to"):
                        raise ValueError(f"{where}: edge without 'to'")
                    edges.append(
                        {
                            "from": str(rec["from"]),
                            "to": str(rec["to"]),
                            "label": rec.get("label") or "RELATED_TO",
                        }
                    )
                else:
                    if not rec.get("id") or not rec.get("type"):
                        raise ValueError(f"{where}: node needs 'id' and 'type'")
                    if not isinstance(rec.get("data"), dict):
                        raise ValueError(f"{where}: node 'data' must be an object")
                    node_id = str(rec["id"])
                    if node_id in nodes:
                        # Later records win, like a re-ingest would.
                        nodes[node_id]["data"].update(rec["data"])
                    else:
                        nodes[node_id] = {"type": rec["type"], "data": rec["data"]}

    missing = {e[k] for e in edges for k in ("from", "to")} - nodes.keys()
    if missing:
        sample = ", ".join(sorted(missing)[:10])
        raise ValueError(f"{len(missing)} edge endpoints have no node record: {sample}")
    return nodes, edges


def plan_chunks(
    nodes: Dict[str, Node], edges: List[Edge], max_nodes: int, max_edges: int
) -> Iterator[Tuple[List[str], List[Edge]]]:
    """Yield (node ids, edges) chunks; each chunk carries its edges' endpoints."""
    linked = set()
    chunk_nodes: Dict[str, None] = {}
    chunk_edges: List[Edge] = []
    for edge in edges:
        new = [n for n in (edge["from"], edge["to"]) if n not in chunk_nodes]
        if chunk_edges and (
            len(chunk_edges) >= max_edges or len(chunk_nodes) + len(new) > max_nodes
        ):
            yield list(chunk_nodes), chunk_edges
            chunk_nodes, chunk_edges = {}, []
        chunk_nodes.update(dict.fromkeys((edge["from"], edge["to"])))
        chunk_edges.append(edge)
        linked.update((edge["from"], edge["to"]))
    if chunk_edges:
        yield list(chunk_nodes), chunk_edges

    orphans = [n for n in nodes if n not in linked]
    for i in range(0, len(orphans), max_nodes):
        yield orphans[i : i + max_nodes], []


def build_payload(node_ids: List[str], edges: List[Edge], nodes: Dict[str, Node]):
    return {
        "nodes": [
            {
                "id": nid,
                "node_id": nid,
                "entity_type": nodes[nid]["type"],
                "nodeLabel": nid,
                "include": True,
                "data": nodes[nid]["data"],
            }
            for nid in node_ids
        ],
        "edges": [
            {"from_id": e["from"], "to_id": e["to"], "label": e["label"]} for e in edges
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="JSONL input files")
    parser.add_argument("--api", default="http://localhost:5001")
    parser.add_argument("--email", required=True)
    parser.add_argument("--sketch-id", required=True)
    parser.add_argument("--max-nodes", type=int, default=2000)
    parser.add_argument("--max-edges", type=int, default=2000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--errors-file", default="bulk_ingest_errors.log")
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and plan chunks, send nothing"
    )
    args = parser.parse_args()

    nodes, edges = read_records(args.files)
    chunks = list(plan_chunks(nodes, edges, args.max_nodes, args.max_edges))
    sent_nodes = sum(len(n) for n, _ in chunks)
    print(
        f"{len(nodes)} nodes, {len(edges)} edges -> {len(chunks)} requests "
        f"({sent_nodes} node writes incl. re-sends)"
    )
    if args.dry_run:
        return 0

    password = os.environ.get("FLOWSINT_PASSWORD")
    if not password:
        print("Set FLOWSINT_PASSWORD", file=sys.stderr)
        return 2
    client = FlowsintClient(args.api, args.email, password, args.timeout)
    client.login()

    failed_chunks = 0
    with open(args.errors_file, "w", encoding="utf-8") as err_log:
        for i, (node_ids, chunk_edges) in enumerate(chunks, 1):
            started = time.monotonic()
            try:
                result = client.execute_import(
                    args.sketch_id,
                    build_payload(node_ids, chunk_edges, nodes),
                    args.retries,
                )
            except RuntimeError as e:
                failed_chunks += 1
                err_log.write(f"chunk {i}: {e}\n")
                print(f"[{i}/{len(chunks)}] FAILED: {e}", file=sys.stderr)
                continue
            for msg in result.get("errors", []):
                err_log.write(f"chunk {i}: {msg}\n")
            print(
                f"[{i}/{len(chunks)}] {result['status']}: "
                f"{result['nodes_created']} nodes written, "
                f"{len(chunk_edges)} edges sent, "
                f"{len(result.get('errors', []))} errors "
                f"({time.monotonic() - started:.1f}s)"
            )

    if failed_chunks:
        print(f"{failed_chunks} chunks failed, see {args.errors_file}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
