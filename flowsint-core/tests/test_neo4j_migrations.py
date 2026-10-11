"""Keeps the Neo4j index migrations in step with the type registry.

Nodes are MERGEd on (nodeLabel, sketch_id) and every sketch query filters on
sketch_id, so a type without both indexes degrades to label scans as its
sketches grow. A new FlowsintType must come with a migration adding them.
"""

import re
from pathlib import Path

import pytest

from flowsint_types import TYPE_REGISTRY

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "neo4j-migrations"
INDEX = re.compile(r"CREATE INDEX (\w+) IF NOT EXISTS FOR \(n:(\w+)\) ON \(n\.(\w+)\);")


def index_statements():
    return [
        INDEX.match(line.strip())
        for path in sorted(MIGRATIONS_DIR.glob("*.cypher"))
        for line in path.read_text().splitlines()
        if line.strip().startswith("CREATE INDEX")
    ]


def indexed():
    return {(m.group(2), m.group(3)) for m in index_statements() if m}


@pytest.mark.parametrize("node_type", sorted(TYPE_REGISTRY.all_types_lowercase()))
@pytest.mark.parametrize("prop", ["sketch_id", "nodeLabel"])
def test_every_type_is_indexed(node_type, prop):
    assert (node_type, prop) in indexed(), (
        f"No {prop} index for '{node_type}': add one in a new neo4j-migrations file"
    )


def test_index_statements_are_well_formed():
    assert index_statements()
    assert all(index_statements()), "CREATE INDEX line not matching expected form"


def test_index_names_are_unique():
    names = [m.group(1) for m in index_statements()]
    assert len(names) == len(set(names))
